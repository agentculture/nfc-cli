import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// Self-hosted variable faces named by tokens.css (--font-display /
// --font-body). No CDN: bundled via @fontsource-variable/*.
import "@fontsource-variable/fraunces";
import "@fontsource-variable/albert-sans";

import "./culture-design/tokens.css";
import "./styles/app.css";

import App from "./App";

const container = document.getElementById("root");
if (!container) {
  throw new Error("index.html is missing its #root container");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
