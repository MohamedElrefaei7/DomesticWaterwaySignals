import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

// Self-hosted and pinned, so the bundle makes no request to a font CDN at runtime. Phase 10 puts
// Caddy and a CSP in front of this; a stylesheet from fonts.googleapis.com would be the one asset
// that has to be excepted from it.
import "@fontsource/archivo-narrow/400.css";
import "@fontsource/archivo-narrow/600.css";
import "@fontsource/zilla-slab/400.css";
import "@fontsource/zilla-slab/600.css";

import "./styles/tokens.css";
import "./styles/app.css";
import { App } from "./App";

const root = document.getElementById("root");
if (root === null) throw new Error("#root is missing from index.html");

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
