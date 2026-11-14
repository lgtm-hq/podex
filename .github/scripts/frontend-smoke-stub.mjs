import { createServer } from "node:http";

// Only the catalog contract used by SSR. Unexpected requests fail closed.
createServer((request, response) => {
  if (request.url !== "/api/v2/podcasts") {
    response.writeHead(404).end();
    return;
  }
  response.writeHead(200, { "Content-Type": "application/json" });
  response.end(JSON.stringify({ items: [], total: 0, limit: 20, offset: 0 }));
}).listen(8000, "127.0.0.1");
