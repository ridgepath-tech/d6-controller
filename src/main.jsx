import { StrictMode, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import ridgepathIcon from "./assets/ridgepath-icon.png";
import "./styles.css";

const KEY_LAYOUT = [11, 12, 13, 14, 15, 6, 7, 8, 9, 10, 1, 2, 3, 4, 5];
const BUILTIN_ACTION_LABELS = { back: "Back", home: "Home", previous_page: "Prev", next_page: "Next", page_indicator: "Page", sleep: "Sleep" };
const BUILTIN_ACTION_TYPES = new Set(Object.keys(BUILTIN_ACTION_LABELS));
const ACTION_TYPES_WITH_ART = new Set([...BUILTIN_ACTION_TYPES, "navigate", "website", "launch", "open_folder", "hotkey"]);
const VERSION = "0.2.0";

function Icon({ name, size = 18 }) {
  const paths = {
    plus: <path d="M12 5v14M5 12h14" />,
    folder: <><path d="M3.5 6.5h6l1.7 2h9.3v9.2a1.8 1.8 0 0 1-1.8 1.8H3.5z" /><path d="M3.5 6.5v-1A1.5 1.5 0 0 1 5 4h4l1.5 2" /></>,
    grid: <><rect x="4" y="4" width="6" height="6" rx="1" /><rect x="14" y="4" width="6" height="6" rx="1" /><rect x="4" y="14" width="6" height="6" rx="1" /><rect x="14" y="14" width="6" height="6" rx="1" /></>,
    upload: <><path d="M12 16V4" /><path d="m7 9 5-5 5 5" /><path d="M4 20h16" /></>,
    download: <><path d="M12 4v12" /><path d="m7 11 5 5 5-5" /><path d="M4 20h16" /></>,
    chevron: <path d="m8 10 4 4 4-4" />,
    more: <><circle cx="5" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="19" cy="12" r="1" fill="currentColor" stroke="none" /></>,
    sun: <><circle cx="12" cy="12" r="3.5" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>,
    warning: <><path d="m12 3 9 17H3z" /><path d="M12 9v4M12 16h.01" /></>,
    bolt: <path d="m13 2-8 11h6l-1 9 8-12h-6z" />,
    pause: <><path d="M8 5v14M16 5v14" /></>,
    play: <path d="m8 5 11 7-11 7z" />,
    check: <path d="m5 12 4 4L19 6" />,
    refresh: <><path d="M20 11a8 8 0 0 0-14.9-4L4 9" /><path d="M4 4v5h5" /><path d="M4 13a8 8 0 0 0 14.9 4L20 15" /><path d="M20 20v-5h-5" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-1.8 1.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-2.5V20a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1-1.8-1.8.1-.1A1.7 1.7 0 0 0 8.1 15a1.7 1.7 0 0 0-1.6-1H6v-2.5h.2a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1 1.8-1.8.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.6v-.2h2.5v.2a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1 1.8 1.8-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2V14h-.2a1.7 1.7 0 0 0-1.6 1Z" /></>,
    edit: <><path d="m4 20 4.5-1 10.8-10.8a2.1 2.1 0 0 0-3-3L5.5 16z" /><path d="m14.8 6.2 3 3" /></>,
    trash: <><path d="M4 7h16" /><path d="M10 11v6M14 11v6" /><path d="m6 7 1 13h10l1-13" /><path d="M9 7V4h6v3" /></>,
  };
  return <svg aria-hidden="true" className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}

function encode(value) { return encodeURIComponent(value); }
function sceneList(profile) { const scenes = profile?.scenes; return scenes && typeof scenes === "object" && Object.keys(scenes).length ? Object.keys(scenes) : ["default"]; }
function pageList(profile, scene) { const pages = profile?.scenes?.[scene]?.pages; return pages && typeof pages === "object" && Object.keys(pages).length ? Object.keys(pages) : ["main"]; }
function selectedPage(profile, scene, page) { return profile?.scenes?.[scene]?.pages?.[page] || profile?.scenes?.[scene] || profile || {}; }
function keyDefinition(profile, scene, page, key) { const selected = selectedPage(profile, scene, page); const definitions = selected.keys || selected; return definitions?.[String(key)] || {}; }

const NAMED_HOTKEYS = new Set(["CTRL", "CONTROL", "SHIFT", "ALT", "OPTION", "WIN", "WINDOWS", "META", "ENTER", "RETURN", "ESC", "ESCAPE", "TAB", "SPACE", "BACKSPACE", "DELETE", "INSERT", "UP", "DOWN", "LEFT", "RIGHT"]);
function hotkeyDisplayLabel(action) {
  if (action?.label?.trim()) return action.label.trim();
  const keys = Array.isArray(action?.keys) ? action.keys.map((key) => String(key).trim()).filter(Boolean) : [];
  if (keys.length === 1 && keys[0].length > 1 && !NAMED_HOTKEYS.has(keys[0].toUpperCase()) && !/^F(?:[1-9]|1[0-9]|2[0-4])$/i.test(keys[0])) return "Password";
  return keys.join(" + ") || "Hotkey";
}
function actionDisplayLabel(definition) {
  const action = definition?.action;
  if (!action) return "No action";
  if (action.type === "hotkey") return hotkeyDisplayLabel(action);
  if (action.type === "navigate") return action.label || action.page || action.scene || "Navigate";
  if (action.type === "launch") return action.label || action.command || "Launch";
  if (action.type === "open_folder") return action.label || "Folder";
  if (action.type === "website") return action.label || "Website";
  return action.label || BUILTIN_ACTION_LABELS[action.type] || action.type || "Action";
}
function formatEvent(event) {
  if (event.type === "key") return `Key ${String(event.key).padStart(2, "0")}`;
  if (event.type === "apply") return `Applied ${event.count} LCD item${event.count === 1 ? "" : "s"}`;
  if (event.type === "refresh") return "D6 refresh signal";
  if (event.type === "action") return `Key ${String(event.key).padStart(2, "0")} → ${event.action}${event.focused === false ? " · focus not confirmed" : ""}`;
  if (event.type === "error") return "Action error";
  return "Service event";
}
function formatTime(timestamp) { return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(timestamp * 1000)); }

function AuthScreen({ setup, onSubmit, busy, error }) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  return <main className="auth-shell"><section className="auth-card"><img src={ridgepathIcon} alt="RidgePath" className="auth-mark" /><span className="eyebrow">RidgePath local utility</span><h1>{setup ? "Set up your D6 Controller" : "Unlock D6 Controller"}</h1><p>{setup ? "Create the local administrator password used to protect this controller." : "Sign in to manage the connected FIFINE AmpliGame D6 Stream Controller."}</p><form onSubmit={(event) => { event.preventDefault(); onSubmit(password, confirmation); }}><label className="modal-field"><span>{setup ? "New admin password" : "Admin password"}</span><input autoFocus type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={setup ? "new-password" : "current-password"} /></label>{setup && <label className="modal-field"><span>Confirm password</span><input type="password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="new-password" /></label>}{setup && <p className="modal-hint">Use at least 10 characters. The password is never stored directly.</p>}{error && <p className="modal-error">{error}</p>}<button className="primary-button auth-submit" disabled={busy}>{busy ? "Working…" : setup ? "Create password" : "Sign in"}</button></form><p className="auth-attribution">Independent third-party controller for FIFINE hardware. Not affiliated with FIFINE or OpenAI.</p></section></main>;
}

function App() {
  const [auth, setAuth] = useState(null);
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState("");
  const [state, setState] = useState(null);
  const [profileName, setProfileName] = useState("");
  const [profile, setProfile] = useState(null);
  const [scene, setScene] = useState("");
  const [page, setPage] = useState("");
  const [selectedKey, setSelectedKey] = useState(1);
  const [brightness, setBrightness] = useState(42);
  const [events, setEvents] = useState([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const [toast, setToast] = useState("");
  const [busy, setBusy] = useState(false);
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [folderBusy, setFolderBusy] = useState(false);
  const [modal, setModal] = useState(null);
  const modalFileInput = useRef(null);
  const restoreInput = useRef(null);
  const eventRail = useRef(null);
  const scenes = useMemo(() => sceneList(profile), [profile]);
  const pages = useMemo(() => pageList(profile, scene), [profile, scene]);
  const selectedDefinition = useMemo(() => keyDefinition(profile, scene, page, selectedKey), [profile, scene, page, selectedKey]);

  async function api(path, options = {}) {
    const response = await fetch(path, { ...options, credentials: "include" });
    if (response.status === 401) setAuth((current) => current ? { ...current, authenticated: false } : current);
    return response;
  }
  async function refreshAuth() { const response = await api("/api/auth/status"); const result = await response.json(); setAuth(result); return result; }
  async function submitAuth(password, confirmation) {
    setAuthBusy(true); setAuthError("");
    try { const path = auth?.setup_required ? "/api/auth/setup" : "/api/auth/login"; const response = await api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password, confirmation }) }); const result = await response.json(); if (!response.ok) throw new Error(result.error || "Authentication failed"); setAuth({ setup_required: false, authenticated: true }); }
    catch (error) { setAuthError(error.message); }
    finally { setAuthBusy(false); }
  }
  async function refreshState() { const response = await api("/api/state"); if (!response.ok) throw new Error("D6 service is unavailable"); const next = await response.json(); setState(next); if (!profileName && next.profiles?.length) setProfileName(next.profiles[0]); return next; }
  async function loadProfile(name) { if (!name) return; const response = await api(`/api/profiles/${encode(name)}`); if (!response.ok) throw new Error("Could not load profile"); const next = await response.json(); setProfile(next); const nextScenes = sceneList(next); const nextScene = nextScenes.includes(scene) ? scene : nextScenes[0]; const nextPages = pageList(next, nextScene); setScene(nextScene); setPage(nextPages.includes(page) ? page : nextPages[0]); setBrightness(Number(next.device?.brightness ?? 42)); }

  useEffect(() => { refreshAuth().catch((error) => setAuthError(error.message)); }, []);
  useEffect(() => {
    if (!auth?.authenticated) return undefined;
    refreshState().catch((error) => setToast(error.message));
    const stream = new EventSource("/api/events", { withCredentials: true });
    stream.onmessage = (message) => { try { const event = JSON.parse(message.data); setEvents((current) => [...current, event].slice(-80)); } catch { /* keep-alive */ } };
    stream.onerror = () => setState((current) => current ? { ...current, device: { ...current.device, connected: false } } : current);
    return () => stream.close();
  }, [auth?.authenticated]);
  useEffect(() => { if (auth?.authenticated && profileName) loadProfile(profileName).catch((error) => setToast(error.message)); }, [auth?.authenticated, profileName]);
  useEffect(() => { if (!profile) return; const nextScenes = sceneList(profile); const nextScene = nextScenes.includes(scene) ? scene : nextScenes[0]; if (nextScene !== scene) setScene(nextScene); const nextPages = pageList(profile, nextScene); if (!nextPages.includes(page)) setPage(nextPages[0]); }, [profile, scene, page]);
  useEffect(() => { if (autoScroll && eventRail.current) eventRail.current.scrollTop = eventRail.current.scrollHeight; }, [events, autoScroll]);
  useEffect(() => { if (!modal) return undefined; const onKey = (event) => { if (event.key === "Escape") setModal(null); }; window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey); }, [modal]);

  function notify(message) { setToast(message); window.setTimeout(() => setToast(""), 3200); }
  function openStructureModal(kind) { setModal({ mode: "structure", kind, name: "", error: "" }); }
  function openActionModal(key = selectedKey) {
    const definition = keyDefinition(profile, scene, page, key); const action = definition.action && typeof definition.action === "object" ? definition.action : {};
    setSelectedKey(key); setModal({ mode: "action", key, actionType: action.type || "none", targetScene: action.scene || scene, targetPage: action.page || page, keys: Array.isArray(action.keys) ? action.keys.join(" + ") : "", label: action.label || "", fontSize: String(action.font_size ?? 16), command: action.command || "", focusPath: action.focus_path || "", processName: action.process_name || "", url: action.url || "", folderPath: action.path || "", error: "" });
  }
  function updateModal(field, value) { setModal((current) => current ? { ...current, [field]: value, error: "" } : current); }

  async function saveStructure() {
    if (!modal || modal.mode !== "structure") return; const label = modal.kind === "scene" ? "scene" : "page"; const name = modal.name.trim(); if (!name) { updateModal("error", `Enter a name for the new ${label}.`); return; }
    try { const response = await api(`/api/profiles/${encode(profileName)}/structure`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: modal.kind, name, parent: modal.kind === "page" ? scene : undefined }) }); const result = await response.json(); if (!response.ok) throw new Error(result.error || `Could not add ${label}`); setProfile(result); if (modal.kind === "scene") setScene(name); if (modal.kind === "page") setPage(name); setModal(null); notify(`${label[0].toUpperCase()}${label.slice(1)} created`); } catch (error) { updateModal("error", error.message); }
  }

  async function saveAction() {
    if (!modal || modal.mode !== "action" || !profile || !profileName) return; let action;
    const fontSize = Math.max(8, Math.min(28, Number.parseInt(modal.fontSize, 10) || 16)); const common = { ...(modal.label.trim() ? { label: modal.label.trim() } : {}), font_size: fontSize };
    if (modal.actionType === "navigate") { if (!modal.targetScene || !modal.targetPage) { updateModal("error", "Choose a destination scene and page."); return; } action = { type: "navigate", scene: modal.targetScene, page: modal.targetPage, ...common }; }
    else if (modal.actionType === "hotkey") { const keys = modal.keys.split(/\s*\+\s*|\s*,\s*/).map((key) => key.trim()).filter(Boolean); if (!keys.length) { updateModal("error", "Enter at least one key or text value."); return; } action = { type: "hotkey", keys, ...common }; }
    else if (modal.actionType === "launch") { if (!modal.command.trim() && !modal.focusPath.trim()) { updateModal("error", "Enter an app/file command or a focus folder path."); return; } action = { type: "launch", ...(modal.command.trim() ? { command: modal.command.trim() } : {}), ...(modal.focusPath.trim() ? { focus_path: modal.focusPath.trim() } : {}), ...(modal.processName.trim() ? { process_name: modal.processName.trim() } : {}), ...common }; }
    else if (modal.actionType === "open_folder") { if (!modal.folderPath.trim()) { updateModal("error", "Choose a folder first."); return; } action = { type: "open_folder", path: modal.folderPath.trim(), ...common }; }
    else if (modal.actionType === "website") { let parsed; try { parsed = new URL(modal.url.trim()); } catch { updateModal("error", "Enter a valid http:// or https:// URL."); return; } if (!["http:", "https:"].includes(parsed.protocol) || !parsed.host) { updateModal("error", "Website actions require an http:// or https:// URL."); return; } action = { type: "website", url: parsed.toString(), ...common }; }
    else if (BUILTIN_ACTION_TYPES.has(modal.actionType)) action = { type: modal.actionType, ...common };
    const nextProfile = JSON.parse(JSON.stringify(profile)); const selected = selectedPage(nextProfile, scene, page); selected.keys = selected.keys && typeof selected.keys === "object" ? selected.keys : {}; const keyName = String(modal.key);
    const existing = selected.keys[keyName] && typeof selected.keys[keyName] === "object" ? selected.keys[keyName] : {};
    if (modal.actionType === "none") { const { action: ignoredAction, ...withoutAction } = existing; selected.keys[keyName] = withoutAction; } else selected.keys[keyName] = { ...existing, action };
    try { const response = await api(`/api/profiles/${encode(profileName)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(nextProfile) }); const result = await response.json(); if (!response.ok) throw new Error(result.error || "Could not save key action"); setProfile(result); setModal(null); notify(`Action saved for key ${String(modal.key).padStart(2, "0")}`); } catch (error) { updateModal("error", error.message); }
  }
  async function deleteAction() {
    if (!modal || modal.mode !== "action" || !modal.key) return; try { const response = await api(`/api/profiles/${encode(profileName)}/scenes/${encode(scene)}/pages/${encode(page)}/keys/${modal.key}`, { method: "DELETE" }); const result = await response.json(); if (!response.ok) throw new Error(result.error || "Could not delete key action"); setProfile(result.profile); setModal(null); notify(`Action deleted for key ${String(modal.key).padStart(2, "0")}`); } catch (error) { updateModal("error", error.message); }
  }
  async function uploadArtwork(file, key = modal?.key) { if (!file || !profileName || !key) return; try { const response = await api(`/api/profiles/${encode(profileName)}/scenes/${encode(scene)}/pages/${encode(page)}/keys/${key}/image`, { method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file }); const result = await response.json(); if (!response.ok) throw new Error(result.error || "Could not upload artwork"); setProfile(result.profile); notify(`Artwork saved for key ${key}`); } catch (error) { notify(error.message); } }
  async function removeArtwork() { if (!modal?.key) return; try { const response = await api(`/api/profiles/${encode(profileName)}/scenes/${encode(scene)}/pages/${encode(page)}/keys/${modal.key}/image`, { method: "DELETE" }); const result = await response.json(); if (!response.ok) throw new Error(result.error || "Could not remove artwork"); setProfile(result.profile); notify("Custom artwork removed; default action artwork restored."); } catch (error) { notify(error.message); } }
  async function browseFolder() { setFolderBusy(true); try { const response = await api("/api/dialog/folder", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); const result = await response.json(); if (!response.ok) throw new Error(result.error || "Folder selection failed"); if (result.selected) updateModal("folderPath", result.path); } catch (error) { updateModal("error", error.message); } finally { setFolderBusy(false); } }
  async function applyPage() { setBusy(true); try { const response = await api(`/api/profiles/${encode(profileName)}/apply`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scene, page }) }); const result = await response.json(); if (!response.ok) throw new Error(result.error || "Could not apply page"); notify(`Applied ${result.applied} LCD item${result.applied === 1 ? "" : "s"} to the D6`); refreshState().catch(() => {}); } catch (error) { notify(error.message); } finally { setBusy(false); } }
  async function refreshD6() { setRefreshBusy(true); try { const response = await api("/api/device/refresh", { method: "POST" }); const result = await response.json(); if (!response.ok) throw new Error(result.error || "Could not refresh the D6"); notify("Refresh signal sent to the D6"); } catch (error) { notify(error.message); } finally { setRefreshBusy(false); } }
  async function updateBrightness(value) { setBrightness(value); try { const response = await api("/api/device/brightness", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value }) }); const result = await response.json(); if (!response.ok) throw new Error(result.error || "Could not set brightness"); } catch (error) { notify(error.message); } }
  async function savePassword() { if (!modal) return; try { const response = await api("/api/auth/change-password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: modal.password, confirmation: modal.confirmation }) }); const result = await response.json(); if (!response.ok) throw new Error(result.error || "Could not change password"); setModal(null); notify("Admin password changed; other sessions were signed out."); } catch (error) { updateModal("error", error.message); } }
  async function signOut() { await api("/api/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); setAuth({ setup_required: false, authenticated: false }); setProfile(null); setState(null); setModal(null); }
  async function backupConfig() { try { const response = await api("/api/backup/export"); if (!response.ok) throw new Error("Could not create backup"); const blob = await response.blob(); const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = `d6-controller-${new Date().toISOString().slice(0, 10)}.d6config`; anchor.click(); URL.revokeObjectURL(url); notify("Configuration backup downloaded."); } catch (error) { notify(error.message); } }
  async function restoreConfig(file) { if (!file) return; try { const response = await api("/api/backup/restore", { method: "POST", headers: { "Content-Type": "application/zip" }, body: file }); const result = await response.json(); if (!response.ok) throw new Error(result.error || "Could not restore backup"); await refreshState(); if (profileName) await loadProfile(profileName); setModal(null); notify("Configuration restored. Review the active page before applying it."); } catch (error) { updateModal("error", error.message); } }

  if (!auth) return <main className="auth-shell"><section className="auth-card"><img src={ridgepathIcon} alt="RidgePath" className="auth-mark" /><p>Connecting to the local service…</p></section></main>;
  if (!auth.authenticated) return <AuthScreen setup={auth.setup_required} onSubmit={submitAuth} busy={authBusy} error={authError} />;
  const connected = Boolean(state?.device?.connected); const hasAssignedAction = Boolean(selectedDefinition?.action && typeof selectedDefinition.action === "object");
  const imageForKey = (key) => { const definition = keyDefinition(profile, scene, page, key); if (definition.image) return `/api/profiles/${encode(profileName)}/scenes/${encode(scene)}/pages/${encode(page)}/keys/${key}/image?v=${encode(definition.image)}`; if (definition.action && ACTION_TYPES_WITH_ART.has(definition.action.type)) return `/api/profiles/${encode(profileName)}/scenes/${encode(scene)}/pages/${encode(page)}/keys/${key}/preview?v=${encode(JSON.stringify(definition.action))}`; return ""; };
  const modalDefinition = modal?.mode === "action" ? keyDefinition(profile, scene, page, modal.key) : {};
  const modalPreview = modal?.mode === "action" ? imageForKey(modal.key) : "";

  return <div className="app-shell">
    <header className="topbar"><div className="brand"><img src={ridgepathIcon} alt="" className="brand-icon" /><span>D6 Controller</span><span className="local-tag">RIDGEPATH · LOCAL</span></div><div className="connection"><span className={`connection-dot ${connected ? "online" : "offline"}`} />{connected ? "Connected" : "Waiting for D6"}<span className="service-label">localhost service</span><button className="topbar-button" onClick={() => setModal({ mode: "settings", password: "", confirmation: "", error: "" })}><Icon name="settings" size={16} />Settings</button></div></header>
    <div className="layout"><aside className="sidebar">
      <section className="side-section"><div className="side-heading"><span>Profiles</span><button className="icon-button" title="Profiles are stored by the local service"><Icon name="folder" /></button></div><div className="side-list">{(state?.profiles || []).map((name) => <button className={`side-row ${name === profileName ? "active" : ""}`} key={name} onClick={() => setProfileName(name)}><Icon name="folder" size={17} /><span>{name}</span><Icon name="more" size={17} /></button>)}</div></section>
      <section className="side-section"><div className="side-heading"><span>Scenes</span><button className="icon-button" onClick={() => openStructureModal("scene")} title="Add scene"><Icon name="plus" /></button></div><div className="side-list">{scenes.map((name) => <button className={`side-row ${name === scene ? "active" : ""}`} key={name} onClick={() => setScene(name)}><Icon name="grid" size={17} /><span>{name}</span><Icon name="more" size={17} /></button>)}</div></section>
      <section className="side-section pages-section"><div className="side-heading"><span>Pages</span><button className="icon-button" onClick={() => openStructureModal("page")} title="Add page"><Icon name="plus" /></button></div><div className="side-list">{pages.map((name) => <button className={`side-row ${name === page ? "active" : ""}`} key={name} onClick={() => setPage(name)}><Icon name="folder" size={17} /><span>{name}</span><Icon name="more" size={17} /></button>)}</div></section>
      <div className="sidebar-footer"><span className="footer-dot" />Independent third-party controller for FIFINE AmpliGame D6 hardware.</div>
    </aside><main className="workspace">
      <div className="workspace-header"><div className="selector-group"><label htmlFor="scene">Scenes</label><div className="select-wrap"><select id="scene" value={scene} onChange={(event) => setScene(event.target.value)}>{scenes.map((name) => <option key={name}>{name}</option>)}</select><Icon name="chevron" size={17} /></div></div><div className="selector-group"><label htmlFor="page">Pages</label><div className="select-wrap"><select id="page" value={page} onChange={(event) => setPage(event.target.value)}>{pages.map((name) => <option key={name}>{name}</option>)}</select><Icon name="chevron" size={17} /></div></div><button className="primary-button" onClick={applyPage} disabled={busy || refreshBusy || !profileName}><Icon name="download" />{busy ? "Applying…" : "Apply selected page"}</button><button className="secondary-button" onClick={refreshD6} disabled={busy || refreshBusy || !connected}><Icon name="refresh" />{refreshBusy ? "Refreshing…" : "Refresh D6"}</button></div>
      <section className="deck-frame" aria-label="D6 button layout"><div className="deck-grid">{KEY_LAYOUT.map((key) => { const definition = keyDefinition(profile, scene, page, key); const actionLabel = actionDisplayLabel(definition); const image = imageForKey(key); return <button className={`deck-key ${selectedKey === key ? "selected" : ""} ${image ? "has-image" : "empty"}`} key={key} onClick={() => openActionModal(key)} aria-label={`Configure Key ${key}`} title={`Configure Key ${String(key).padStart(2, "0")}`}>{image ? <img src={image} alt="" /> : <span className="empty-art"><Icon name="plus" size={22} /><span>Configure</span></span>}<span className="key-overlay"><strong>{String(key).padStart(2, "0")}</strong><span>{actionLabel}</span></span></button>; })}</div></section>
      <section className="control-strip"><div className="brightness-control"><div className="control-label"><Icon name="sun" size={19} /><span>Brightness</span><strong>{brightness}%</strong></div><input aria-label="Brightness" type="range" min="0" max="100" value={brightness} onChange={(event) => setBrightness(Number(event.target.value))} onMouseUp={(event) => updateBrightness(Number(event.target.value))} onTouchEnd={(event) => updateBrightness(Number(event.target.value))} /></div><div className="capability-note"><Icon name="warning" size={19} /><span>RGB unsupported on this D6</span></div></section>
      <section className="key-inspector"><div><span className="eyebrow">Selected key</span><h2>Key {String(selectedKey).padStart(2, "0")}</h2><p>{hasAssignedAction ? `Action: ${selectedDefinition.action.type}` : "Click any key to configure it."}</p></div><span className="inspector-note">Key editing, artwork, reset, and delete are inside the key modal.</span></section>
    </main><aside className="events-panel"><div className="events-heading"><div><h2>Live events</h2><span>Press and release reports from the D6</span></div><button className="secondary-button compact" onClick={() => setEvents([])}>Clear</button></div><div className="event-list" ref={eventRail}>{events.length ? events.map((event, index) => <div className={`event-row ${event.pressed === false ? "release" : "press"}`} key={`${event.timestamp}-${index}`}><span className="event-symbol"><Icon name={event.pressed === false ? "play" : "pause"} size={13} /></span><div className="event-copy"><strong>{event.type === "key" ? (event.pressed ? "Press" : "Release") : event.type === "refresh" ? "Refresh" : event.type === "error" ? "Error" : "Applied"}</strong><span>{formatEvent(event)}</span></div><time>{formatTime(event.timestamp)}</time></div>) : <div className="empty-events"><Icon name="bolt" size={24} /><p>Press a D6 button to see it here.</p></div>}</div><div className="events-footer"><button className={`toggle-button ${autoScroll ? "on" : ""}`} onClick={() => setAutoScroll((value) => !value)}><span><Icon name={autoScroll ? "check" : "pause"} size={14} /></span></button><span>Auto-scroll</span></div></aside></div>

    {modal && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setModal(null); }}><section className={`modal-dialog ${modal.mode === "action" ? "action-dialog" : ""}`} role="dialog" aria-modal="true" aria-labelledby="modal-title" onMouseDown={(event) => event.stopPropagation()}>
      {modal.mode === "structure" && <form onSubmit={(event) => { event.preventDefault(); saveStructure(); }}><div className="modal-heading"><div><span className="eyebrow">Add {modal.kind}</span><h2 id="modal-title">Create {modal.kind}</h2></div><button type="button" className="icon-button" aria-label="Close dialog" onClick={() => setModal(null)}>×</button></div><label className="modal-field"><span>{modal.kind === "scene" ? "Scene name" : "Page name"}</span><input autoFocus value={modal.name} onChange={(event) => updateModal("name", event.target.value)} placeholder={modal.kind === "scene" ? "Password Entry" : "Codex"} /></label>{modal.kind === "page" && <p className="modal-hint">This page will be added under scene <strong>{scene}</strong>.</p>}{modal.error && <p className="modal-error">{modal.error}</p>}<div className="modal-actions"><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button type="submit" className="primary-button">Create {modal.kind}</button></div></form>}
      {modal.mode === "settings" && <div><div className="modal-heading"><div><span className="eyebrow">Administration</span><h2 id="modal-title">Settings</h2></div><button type="button" className="icon-button" aria-label="Close dialog" onClick={() => setModal(null)}>×</button></div><section className="settings-section"><h3>Security</h3><p>Localhost authentication is enabled. Sessions expire after eight hours and password changes invalidate other sessions.</p><form onSubmit={(event) => { event.preventDefault(); savePassword(); }}><label className="modal-field"><span>New admin password</span><input type="password" value={modal.password} onChange={(event) => updateModal("password", event.target.value)} autoComplete="new-password" /></label><label className="modal-field"><span>Confirm password</span><input type="password" value={modal.confirmation} onChange={(event) => updateModal("confirmation", event.target.value)} autoComplete="new-password" /></label>{modal.error && <p className="modal-error">{modal.error}</p>}<button className="secondary-button" type="submit">Change Admin Password</button></form></section><section className="settings-section"><h3>Configuration</h3><p>Backups include profiles and LCD artwork, but never the administrator hash or session tokens. Password-entry actions may contain sensitive text.</p><button className="secondary-button" onClick={backupConfig}><Icon name="download" />Backup Configuration</button><input ref={restoreInput} type="file" accept=".d6config,application/zip" hidden onChange={(event) => { restoreConfig(event.target.files?.[0]); event.target.value = ""; }} /><button className="secondary-button" onClick={() => restoreInput.current?.click()}><Icon name="upload" />Restore Configuration</button></section><section className="settings-section"><h3>About</h3><p>RidgePath D6 Controller v{VERSION}</p><p className="modal-hint">Independent third-party software for the FIFINE AmpliGame D6 Stream Controller. Not affiliated with or endorsed by FIFINE or OpenAI. Product and company names identify compatible hardware and services.</p></section><div className="modal-actions"><button type="button" className="danger-button secondary-button" onClick={signOut}>Sign out</button><button type="button" className="primary-button" onClick={() => setModal(null)}>Done</button></div></div>}
      {modal.mode === "action" && <form onSubmit={(event) => { event.preventDefault(); saveAction(); }}><div className="modal-heading"><div><span className="eyebrow">Key {String(modal.key).padStart(2, "0")}</span><h2 id="modal-title">Configure Key {String(modal.key).padStart(2, "0")}</h2></div><button type="button" className="icon-button" aria-label="Close dialog" onClick={() => setModal(null)}>×</button></div><div className="action-editor-grid"><div><label className="modal-field"><span>Action</span><select autoFocus value={modal.actionType} onChange={(event) => updateModal("actionType", event.target.value)}><option value="none">No action</option><option value="navigate">Navigate to scene/page</option><option value="back">Predefined back button</option><option value="home">Predefined home button</option><option value="previous_page">Previous page</option><option value="next_page">Next page</option><option value="page_indicator">Page indicator</option><option value="sleep">Sleep deck display</option><option value="website">Open website</option><option value="open_folder">Open Folder</option><option value="launch">Open app, file, or folder</option><option value="hotkey">Send hotkey or text</option></select></label>{modal.actionType === "navigate" && <div className="modal-two-column"><label className="modal-field"><span>Scene</span><select value={modal.targetScene} onChange={(event) => setModal((current) => current ? { ...current, targetScene: event.target.value, targetPage: pageList(profile, event.target.value)[0], error: "" } : current)}>{scenes.map((name) => <option key={name}>{name}</option>)}</select></label><label className="modal-field"><span>Page</span><select value={modal.targetPage} onChange={(event) => updateModal("targetPage", event.target.value)}>{pageList(profile, modal.targetScene).map((name) => <option key={name}>{name}</option>)}</select></label></div>}{modal.actionType === "website" && <label className="modal-field"><span>Website URL</span><input value={modal.url} onChange={(event) => updateModal("url", event.target.value)} placeholder="https://example.com" /></label>}{modal.actionType === "open_folder" && <div className="folder-picker"><label className="modal-field"><span>Selected folder</span><input value={modal.folderPath} readOnly placeholder="Choose a folder" /></label><button type="button" className="secondary-button" onClick={browseFolder} disabled={folderBusy}>{folderBusy ? "Opening…" : "Browse…"}</button></div>}{modal.actionType === "launch" && <><label className="modal-field"><span>Command</span><input value={modal.command} onChange={(event) => updateModal("command", event.target.value)} placeholder="notepad.exe or an absolute app/file path" /></label><label className="modal-field"><span>Focus folder path (optional)</span><input value={modal.focusPath} onChange={(event) => updateModal("focusPath", event.target.value)} placeholder="Legacy Explorer focus path" /></label><label className="modal-field"><span>Process name (optional)</span><input value={modal.processName} onChange={(event) => updateModal("processName", event.target.value)} placeholder="ChatGPT.exe" /></label></>}{modal.actionType === "hotkey" && <label className="modal-field"><span>Keys or text</span><input value={modal.keys} onChange={(event) => updateModal("keys", event.target.value)} placeholder="CTRL + SHIFT + F1 or password text" /></label>}{modal.actionType !== "none" && <><label className="modal-field"><span>LCD label (optional)</span><input value={modal.label} onChange={(event) => updateModal("label", event.target.value)} placeholder={modal.actionType === "open_folder" ? "Projects" : "Action label"} /></label><label className="modal-field"><span>LCD font size (px)</span><input type="number" min="8" max="28" step="1" value={modal.fontSize} onChange={(event) => updateModal("fontSize", event.target.value)} /></label></>}</div><div className="lcd-preview"><span className="eyebrow">LCD preview</span>{modalPreview ? <img src={modalPreview} alt="Generated D6 LCD preview" /> : <div className="preview-empty">No action artwork</div>}</div></div>{modal.error && <p className="modal-error">{modal.error}</p>}<div className="artwork-controls"><input ref={modalFileInput} type="file" accept="image/*" hidden onChange={(event) => { uploadArtwork(event.target.files?.[0]); event.target.value = ""; }} /><span className="eyebrow">LCD appearance</span><button type="button" className="secondary-button" onClick={() => modalFileInput.current?.click()}><Icon name="upload" />{modalDefinition.image ? "Replace Artwork" : "Upload Artwork"}</button><button type="button" className="secondary-button" onClick={removeArtwork} disabled={!modalDefinition.image}>Reset to default</button><span className="modal-hint">Generated artwork uses the same 100×100 image sent to the D6. Custom artwork overrides it.</span></div><div className="modal-actions modal-actions-split">{hasAssignedAction && <button type="button" className="danger-button secondary-button" onClick={deleteAction}><Icon name="trash" />Delete Action</button>}<span /><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button type="submit" className="primary-button">Save</button></div></form>}
    </section></div>}
    {toast && <div className="toast"><Icon name="check" size={16} />{toast}</div>}
  </div>;
}

createRoot(document.getElementById("root")).render(<StrictMode><App /></StrictMode>);
