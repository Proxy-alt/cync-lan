/**
 * Device-export UI.
 *
 * Ported from the inline <script> the page used to carry. Behaviour is
 * deliberately unchanged - same endpoints, same messages, same flow - but the
 * handlers are bound here rather than through onclick attributes, so the
 * bundle can be hashed and served with a strict Content-Security-Policy.
 */

import Prism from "prismjs";
import "prismjs/components/prism-yaml";
import "./style.css";

/** Throws rather than returning null, so a renamed id fails loudly at load. */
function el<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`missing element #${id}`);
  return node as T;
}

const toast = el("toast");
const errorBox = el("error");

let toastTimer: number | undefined;
let errorTimer: number | undefined;

function showToast(msg: string): void {
  toast.textContent = msg;
  toast.classList.remove("hidden");
  // Clearing first matters: two toasts in quick succession used to share one
  // timer, so the second was hidden early by the first one's timeout.
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.add("hidden"), 4000);
}

function showError(message: string): void {
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
  window.clearTimeout(errorTimer);
  errorTimer = window.setTimeout(() => errorBox.classList.add("hidden"), 3000);
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

async function copyConfig(): Promise<void> {
  const code = document.querySelector("#yamlPreview code");
  const yamlText = code?.textContent?.trim() ?? "";
  try {
    await navigator.clipboard.writeText(yamlText);
    showToast("Config copied to clipboard!");
  } catch (e) {
    showError("Copy failed: " + message(e));
  }
}

async function fetchAndShowConfig(): Promise<void> {
  try {
    const response = await fetch("./api/export/download");
    const text = await response.text();
    const code = document.querySelector("#yamlPreview code");
    if (code) {
      code.textContent = text;
      Prism.highlightElement(code);
    }
    el<HTMLAnchorElement>("downloadLink").href = "./api/export/download";
    el("successSection").classList.remove("hidden");
  } catch (e) {
    showError("Failed to load config: " + message(e));
  }
}

async function startExport(): Promise<void> {
  const btn = el<HTMLButtonElement>("startButton");
  const spinner = el("spinner");
  btn.disabled = true;
  spinner.classList.remove("hidden");

  try {
    const response = await fetch("./api/export/start");
    const result = await response.json();
    if (response.ok && result.success) {
      await fetchAndShowConfig();
    } else if (response.ok && String(result.message ?? "").includes("OTP")) {
      showToast(result.message);
      el("otpInput").focus();
    } else {
      showError(result.detail || "Export failed.");
    }
  } catch (e) {
    showError("Export error: " + message(e));
  } finally {
    btn.disabled = false;
    spinner.classList.add("hidden");
  }
}

async function submitOTP(): Promise<void> {
  const otp = el<HTMLInputElement>("otpInput").value.trim();
  if (!/^[0-9]{4,10}$/.test(otp)) {
    showError("OTP must be 4–10 digits");
    return;
  }

  try {
    const response = await fetch("./api/export/otp/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ otp: parseInt(otp, 10) }),
    });
    const result = await response.json();
    if (response.ok && result.success) {
      el("restartDivider").classList.remove("hidden");
      el("restartButton").classList.remove("hidden");
      await fetchAndShowConfig();
    } else {
      showError(result.detail || "OTP failed.");
    }
  } catch (e) {
    showError("OTP submit error: " + message(e));
  }
}

async function restartServer(): Promise<void> {
  try {
    const response = await fetch("./api/restart", { method: "POST" });
    if (response.ok) showToast("Server restart requested.");
  } catch (e) {
    showError("Restart error: " + message(e));
  }
}

el("startButton").addEventListener("click", () => void startExport());
el("submitOtpButton").addEventListener("click", () => void submitOTP());
el("copyButton").addEventListener("click", () => void copyConfig());
el("restartButton").addEventListener("click", () => void restartServer());

el("otpInput").addEventListener("keydown", (e) => {
  if ((e as KeyboardEvent).key === "Enter") void submitOTP();
});
