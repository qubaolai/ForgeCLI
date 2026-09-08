/** 按源码路径加载一个模块。
 *
 * 走 vite 解析而不是 node 直接读 .ts: 源码里用的是 `@/` 别名, node 认不出来。
 * 每个测试文件是一个独立进程, 所以这里的 server 也是每个文件一个。
 */

import { after } from "node:test";
import { createServer } from "vite";

const server = await createServer({
  server: { middlewareMode: true, ws: false },
  optimizeDeps: { noDiscovery: true, include: [] },
  appType: "custom",
});
after(() => server.close());

export function load(path) {
  return server.ssrLoadModule(path);
}
