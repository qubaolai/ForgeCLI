/** 供应商页。
 *
 * 自建的单独一组: 它们没有注册表默认值可以回落, 而且用户需要一眼看出哪几家是自己加的。
 * 内置与自建由后端的 builtin 标记分开 —— 前端手抄一份内置清单的话, 加一家内置供应商
 * 就会让它出现在"自建"那一组下, 而不会有任何东西报错。
 */

import type { Administration } from "@/features/administration/useAdministration";
import { ProviderEditor } from "@/features/settings/models/ProviderEditor";

export function ProvidersView({ admin, onOpenAdd }: { admin: Administration; onOpenAdd: () => void }) {
  const builtinProviders = admin.knownProviders.filter((item) => item.builtin !== false);
  const customProviders = admin.knownProviders.filter((item) => item.builtin === false);

  return (
    <>
      <div className="model-page-heading">
        <div>
          <h3>供应商</h3>
          <p>配置兼容端点与密钥环境变量；API 密钥不会在页面中读取或保存。</p>
        </div>
        <button type="button" className="primary-action" onClick={() => onOpenAdd()}>
          ＋ 添加供应商
        </button>
      </div>
      <div className="provider-availability-list">
        {builtinProviders.map((known) => {
          const provider = admin.providerSettings.find((item) => item.id === known.id);
          if (!provider) return null;
          return (
            <ProviderEditor
              key={known.id}
              provider={provider}
              known={known}
              fields={admin.providerFields}
              onSaveProvider={admin.saveProviderFields}
            />
          );
        })}
      </div>
      {/* 自建的单独一组: 它们没有注册表默认值可以回落, 而且用户需要一眼看出哪几家是自己加的。
      可用性一律从 knownProviders 里取 —— 这里曾经自己编过一个 available: true,
      于是一家根本没配环境变量的供应商在界面上显示"密钥已就绪"。 */}
      {customProviders.length > 0 && (
        <>
          <h4 className="provider-group-heading">自建供应商</h4>
          <div className="provider-availability-list">
            {customProviders.map((known) => {
              const provider = admin.providerSettings.find((item) => item.id === known.id);
              if (!provider) return null;
              return (
                <ProviderEditor
                  key={known.id}
                  provider={provider}
                  known={known}
                  fields={admin.providerFields}
                  onSaveProvider={admin.saveProviderFields}
                />
              );
            })}
          </div>
        </>
      )}
      <p className="field-help provider-protocol-help">
        目前只有 OpenAI Compatible <code>/chat/completions</code> 协议有对应的适配器；Ollama、vLLM
        等本地端点属于这一类。
      </p>
    </>
  );
}
