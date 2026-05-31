# 使用预构建的 dist 目录，避免服务器 Node.js 编译
FROM nginx:1.27-alpine

COPY dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
