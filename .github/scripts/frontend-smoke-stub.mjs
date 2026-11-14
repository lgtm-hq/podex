import { createServer } from "node:http";

import catalog from "./frontend-smoke-catalog.json" with { type: "json" };

// Only the catalog contract used by SSR. Unexpected requests fail closed.
createServer((request, response) => {
  if (request.url !== "/api/v2/podcasts") {
    response.writeHead(404).end();
    return;
  }
  response.writeHead(200, { "Content-Type": "application/json" });
  response.end(JSON.stringify(catalog));
}).listen(8000, "127.0.0.1");
