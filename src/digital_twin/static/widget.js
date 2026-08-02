(() => {
  "use strict";
  if (window.__prathameshTwinWidget) return;
  window.__prathameshTwinWidget = true;

  const script = document.currentScript;
  const source = new URL(script.src);
  const base = script.dataset.twinUrl || source.origin;
  const position = script.dataset.position === "left" ? "left" : "right";
  const accent = script.dataset.color || "#c8ff48";

  const root = document.createElement("div");
  root.setAttribute("data-prathamesh-twin", "");
  Object.assign(root.style, {
    position: "fixed",
    zIndex: "2147483000",
    bottom: "20px",
    [position]: "20px",
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
  });

  const frame = document.createElement("iframe");
  frame.src = `${base.replace(/\/$/, "")}/embed`;
  frame.title = "Chat with Prathamesh's digital twin";
  frame.allow = "clipboard-write";
  Object.assign(frame.style, {
    display: "none",
    width: "min(430px, calc(100vw - 24px))",
    height: "min(720px, calc(100vh - 92px))",
    marginBottom: "12px",
    border: "1px solid rgba(255,255,255,.16)",
    borderRadius: "22px",
    background: "#07090d",
    boxShadow: "0 28px 90px rgba(0,0,0,.45)",
  });

  const button = document.createElement("button");
  button.type = "button";
  button.setAttribute("aria-expanded", "false");
  button.setAttribute("aria-label", "Open Prathamesh's digital twin");
  button.textContent = script.dataset.label || "ASK PK.TWIN  ↗";
  Object.assign(button.style, {
    float: position,
    minHeight: "52px",
    padding: "0 18px",
    border: "0",
    borderRadius: "16px",
    color: "#111508",
    background: accent,
    boxShadow: `0 12px 35px ${accent}33`,
    cursor: "pointer",
    font: "700 11px ui-monospace, SFMono-Regular, Consolas, monospace",
    letterSpacing: ".08em",
  });
  button.addEventListener("click", () => {
    const open = frame.style.display !== "none";
    frame.style.display = open ? "none" : "block";
    button.setAttribute("aria-expanded", String(!open));
    button.textContent = open ? (script.dataset.label || "ASK PK.TWIN  ↗") : "CLOSE TWIN  ×";
  });
  root.append(frame, button);
  document.body.append(root);
})();

