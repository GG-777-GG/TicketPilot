# 文件职责：构建React前端，并用Nginx提供生产页面。
# 作用与配合：从仓库根目录构建，由compose.demo.yaml启动。
# 边界：最终镜像只提供静态页面与接口代理，不运行Vite开发服务器。

# 第一阶段：使用Node构建前端。
FROM node:24-bookworm-slim AS build

WORKDIR /app

# 先安装锁文件确定的依赖，方便复用构建缓存。
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# 再复制前端源码，执行类型检查和生产构建。
COPY frontend/ ./
RUN npm run build

# 第二阶段：使用Nginx运行。
FROM nginx:stable-alpine AS runtime

# 安装我们编写的站点配置。
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

# 只从构建阶段复制dist，不复制Node和node_modules。
COPY --from=build /app/dist/ /usr/share/nginx/html/

EXPOSE 80

# 前台运行，让Nginx作为容器的主进程。
CMD ["nginx", "-g", "daemon off;"]