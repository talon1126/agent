# 部署约定

前端是 Vite 静态应用，构建产物为 `apps/talonmart-web/dist`。

当前部署选择：

- 面试展示优先使用 Netlify 托管静态前端。
- 仓库根目录的 `netlify.toml` 指定 `base=apps/talonmart-web`、`command=pnpm build`、`publish=dist`。
- 后端 API 单独部署，前端通过 Netlify 环境变量配置 API Base URL，例如 `VITE_API_BASE_URL`。
- Vue Router 使用 history 模式时，Netlify 需要把 `/*` rewrite 到 `/index.html`，避免刷新子路由 404。
- 只有在需要展示完整工程部署能力时，再增加 Docker + Nginx 托管前端静态产物。

前端不要求一开始放进 Docker。若后续使用 Docker，应避免把运行时业务逻辑放进前端容器；前端容器只负责服务静态文件。
