import React from "react";
import * as ReactDOM from "react-dom";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";
import { buildBackendHttpUrl } from "./lib/config";
import { initializeTheme, watchSystemTheme } from "./lib/theme";

const chatkitScript = document.createElement("script");
chatkitScript.src = buildBackendHttpUrl("/api/chatkit/assets/chatkit.js");
chatkitScript.async = true;
chatkitScript.crossOrigin = "anonymous";
document.head.appendChild(chatkitScript);

// Initialize theme before React renders to prevent flash of wrong theme
initializeTheme();

// Keep the theme in sync with the OS color scheme when "system" is selected,
// so light/dark switches apply live without requiring a page refresh.
watchSystemTheme();

// Make React and ReactDOM globally available immediately (not in useEffect)
window.React = React;
window.ReactDOM = ReactDOM;

const root = createRoot(document.getElementById("root") as HTMLElement);
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

// If you want to start measuring performance in your app, pass a function
// to log results (for example: reportWebVitals(console.log))
// or send to an analytics endpoint. Learn more: https://bit.ly/CRA-vitals
