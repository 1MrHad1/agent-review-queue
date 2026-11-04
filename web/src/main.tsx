import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { ReviewScreen } from "./ReviewScreen";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

createRoot(root).render(
  <StrictMode>
    <ReviewScreen reviewer="dr-smith" />
  </StrictMode>,
);
