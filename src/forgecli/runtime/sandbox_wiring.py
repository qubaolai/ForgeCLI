"""一个工具栈的沙箱运行件: 实例临时目录, 本机代理, 围栏工厂.

把设置, 权限规则与工作目录编译成 sandbox-runtime 的配置, 起代理, 接"不认识的域名问用户"的回调. 
这里的三样东西都跟着工具栈生灭:

- **临时目录**: `mkdtemp` 一个, 每条围栏内的命令都能写, 跨命令持久, `close` 时删.
  子进程的 TMPDIR 指向它 (由 Provider 的 `environment_for` 注入).
- **代理**: 第一次要围栏时才起 (没开沙箱就永远不起). macOS 监听本机端口; Linux 监听
  临时目录里的 unix socket, 由 bubblewrap bind 进围栏.
- **围栏工厂**: 每条命令现算, 因为权限规则与额外目录会话内会变.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from forgecli.common.observability.log import get_log
from forgecli.common.runtime_files import runtime_dir
from forgecli.core.app_state import AppState
from forgecli.core.execution.fence import FencePolicy, NetworkFence, fence_for
from forgecli.core.sandbox import SandboxSettings
from forgecli.core.tool.tool import ToolUseContext
from forgecli.execution.sandbox.network_proxy import DomainPolicy, NetworkProxy
from forgecli.execution.sandbox.provider import SandboxProvider
from forgecli.human_interaction.prompt import HumanPrompt, PromptChoice, PromptKind
from forgecli.human_interaction.service import HumanPromptService
from forgecli.security.sandbox_paths import sandbox_paths_for
from forgecli.security.settings_store import add_sandbox_allowed_domain

__all__ = ["SandboxRuntime"]

_log = get_log(__name__)

_CHOICE_YES = "yes"
_CHOICE_ALWAYS = "yes_always"
_CHOICE_NO = "no"


class SandboxRuntime:
    def __init__(
        self,
        *,
        provider: SandboxProvider,
        workspace_roots: tuple[str, ...],
        protected_paths: tuple[str, ...],
        get_app_state: Callable[[], AppState],
        set_app_state: Callable[[AppState], None],
        prompts: HumanPromptService | None,
        project_settings_file: Path,
    ) -> None:
        self._provider = provider
        self._roots = workspace_roots
        self._protected = protected_paths
        self._get_state = get_app_state
        self._set_state = set_app_state
        self._settings_file = project_settings_file
        self.temp_dir = tempfile.mkdtemp(prefix="sandbox-", dir=runtime_dir())
        self._policy = DomainPolicy(ask=self._ask_host if prompts is not None else None)
        self._prompts = prompts
        self._proxy: NetworkProxy | None = None

    # ---- 围栏 ----

    def fence(self, context: ToolUseContext) -> FencePolicy | None:
        """给一条命令算围栏. 没开沙箱 (或没立起来) 返回 None."""
        state = context.get_app_state()
        sandbox = state.sandbox
        if not sandbox.active:
            return None
        permission = state.tool_permission_context
        extra = tuple(item.path for item in permission.additional_working_directories)
        roots = self._roots + extra
        paths = sandbox_paths_for(
            permission,
            workspace_roots=roots,
            allow_write=sandbox.allow_write,
            deny_write=sandbox.deny_write,
            deny_read=sandbox.deny_read,
            allow_read=sandbox.allow_read,
        )
        return fence_for(
            sandbox,
            workspace_roots=roots,
            protected_paths=self._protected,
            extra_writable=paths.extra_writable,
            denied_write=paths.denied_write,
            denied_read=paths.denied_read,
            allowed_read=paths.allowed_read,
            instance_temp_root=self.temp_dir,
            network=self._network(sandbox),
        )

    def _network(self, sandbox: SandboxSettings) -> NetworkFence:
        fence = NetworkFence(
            unrestricted=sandbox.network_unrestricted,
            allow_unix_sockets=sandbox.allow_unix_sockets,
            allow_all_unix_sockets=sandbox.allow_all_unix_sockets,
            allow_local_binding=sandbox.allow_local_binding,
        )
        if fence.unrestricted:
            return fence
        self._policy.update(sandbox.allowed_domains, sandbox.denied_domains)
        proxy = self._ensure_proxy()
        if proxy is None:
            return fence
        return replace(fence, proxy_port=proxy.port, proxy_socket=proxy.socket_path)

    def _ensure_proxy(self) -> NetworkProxy | None:
        if self._proxy is not None:
            return self._proxy
        name = self._provider.name
        if name == "seatbelt":
            proxy = NetworkProxy(self._policy)
            proxy.start()
        elif name == "bubblewrap":
            proxy = NetworkProxy(self._policy)
            proxy.start(unix_socket=str(Path(self.temp_dir) / "proxy.sock"))
        else:
            # WSL2 / 无围栏: 没有能把代理送进围栏的办法, 网络就是全拦 (或 `*` 全放).
            return None
        self._proxy = proxy
        return proxy

    # ---- 不认识的域名问人 ----

    def _ask_host(self, host: str, port: int) -> bool:
        assert self._prompts is not None
        prompt = HumanPrompt(
            prompt_id=f"net_{uuid.uuid4().hex[:12]}",
            kind=PromptKind.APPROVAL,
            title=f"网络访问: {host}",
            body=(
                f"围栏里的命令要连 {host}:{port}, 它不在沙箱设置的 allowedDomains 里. "
                "允许这次连接吗?"
            ),
            choices=(
                PromptChoice(_CHOICE_YES, "允许 (本会话内不再问这个域名)"),
                PromptChoice(
                    _CHOICE_ALWAYS,
                    f"允许, 并把 {host} 写进项目的沙箱设置",
                    "以后的会话也不再问",
                ),
                PromptChoice(_CHOICE_NO, "拒绝"),
            ),
            detail={"kind": "sandbox_network", "host": host, "port": port},
        )
        answer = self._prompts.ask(prompt)
        if not answer.resolved or answer.choice == _CHOICE_NO:
            return False
        if answer.choice == _CHOICE_ALWAYS:
            self._remember_domain(host)
        return True

    def _remember_domain(self, host: str) -> None:
        add_sandbox_allowed_domain(self._settings_file, host)
        state = self._get_state()
        sandbox = state.sandbox
        if host not in sandbox.allowed_domains:
            sandbox = replace(sandbox, allowed_domains=(*sandbox.allowed_domains, host))
            self._set_state(replace(state, sandbox=sandbox))
        self._policy.update(sandbox.allowed_domains, sandbox.denied_domains)
        _log.info("sandbox.domain_persisted", host=host, file=str(self._settings_file))

    # ---- 收尾 ----

    def close(self) -> None:
        if self._proxy is not None:
            self._proxy.close()
            self._proxy = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)
