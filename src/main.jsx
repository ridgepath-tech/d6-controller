import { StrictMode, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const KEY_COUNT = 15;
const KEY_LAYOUT = [11, 12, 13, 14, 15, 6, 7, 8, 9, 10, 1, 2, 3, 4, 5];

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
    edit: <><path d="m4 20 4.5-1 10.8-10.8a2.1 2.1 0 0 0-3-3L5.5 16z" /><path d="m14.8 6.2 3 3" /></>,
    trash: <><path d="M4 7h16" /><path d="M10 11v6M14 11v6" /><path d="m6 7 1 13h10l1-13" /><path d="M9 7V4h6v3" /></>,
  };
  return <svg aria-hidden="true" className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}

function encode(value) {
  return encodeURIComponent(value);
}

function sceneList(profile) {
  const scenes = profile?.scenes;
  return scenes && typeof scenes === "object" && Object.keys(scenes).length ? Object.keys(scenes) : ["default"];
}

function pageList(profile, scene) {
  const pages = profile?.scenes?.[scene]?.pages;
  return pages && typeof pages === "object" && Object.keys(pages).length ? Object.keys(pages) : ["main"];
}

function selectedPage(profile, scene, page) {
  const sceneData = profile?.scenes?.[scene];
  const pageData = sceneData?.pages?.[page];
  if (pageData) return pageData;
  return sceneData || profile || {};
}

function keyDefinition(profile, scene, page, key) {
  const selected = selectedPage(profile, scene, page);
  const definitions = selected.keys || selected;
  return definitions?.[String(key)] || {};
}

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
  return action.type || "Action";
}

function formatEvent(event) {
  if (event.type === "key") return `Key ${String(event.key).padStart(2, "0")}`;
  if (event.type === "apply") return `Applied ${event.count} image${event.count === 1 ? "" : "s"}`;
  if (event.type === "refresh") return "D6 refresh signal";
  if (event.type === "action") return `Key ${String(event.key).padStart(2, "0")} → ${event.action}`;
  if (event.type === "error") return "Action error";
  return "Service event";
}

function formatTime(timestamp) {
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(timestamp * 1000));
}

function App() {
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
  const [modal, setModal] = useState(null);
  const fileInput = useRef(null);
  const eventRail = useRef(null);

  const scenes = useMemo(() => sceneList(profile), [profile]);
  const pages = useMemo(() => pageList(profile, scene), [profile, scene]);
  const selectedDefinition = useMemo(() => keyDefinition(profile, scene, page, selectedKey), [profile, scene, page, selectedKey]);

  async function refreshState() {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error("D6 service is unavailable");
    const next = await response.json();
    setState(next);
    if (!profileName && next.profiles?.length) setProfileName(next.profiles[0]);
    return next;
  }

  async function loadProfile(name) {
    if (!name) return;
    const response = await fetch(`/api/profiles/${encode(name)}`);
    if (!response.ok) throw new Error("Could not load profile");
    const next = await response.json();
    setProfile(next);
    const nextScenes = sceneList(next);
    const nextScene = nextScenes.includes(scene) ? scene : nextScenes[0];
    const nextPages = pageList(next, nextScene);
    setScene(nextScene);
    setPage(nextPages.includes(page) ? page : nextPages[0]);
    setBrightness(Number(next.device?.brightness ?? 42));
  }

  useEffect(() => {
    refreshState().catch((error) => setToast(error.message));
    const stream = new EventSource("/api/events");
    stream.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data);
        setEvents((current) => [...current, event].slice(-80));
      } catch {
        // Ignore malformed keep-alive payloads.
      }
    };
    stream.onerror = () => setState((current) => current ? { ...current, device: { ...current.device, connected: false } } : current);
    return () => stream.close();
  }, []);

  useEffect(() => {
    if (profileName) loadProfile(profileName).catch((error) => setToast(error.message));
  }, [profileName]);

  useEffect(() => {
    if (!profile) return;
    const nextScenes = sceneList(profile);
    const nextScene = nextScenes.includes(scene) ? scene : nextScenes[0];
    if (nextScene !== scene) setScene(nextScene);
    const nextPages = pageList(profile, nextScene);
    if (!nextPages.includes(page)) setPage(nextPages[0]);
  }, [profile, scene, page]);

  useEffect(() => {
    if (autoScroll && eventRail.current) eventRail.current.scrollTop = eventRail.current.scrollHeight;
  }, [events, autoScroll]);

  useEffect(() => {
    if (!modal) return undefined;
    function handleEscape(event) {
      if (event.key === "Escape") setModal(null);
    }
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [modal]);

  function notify(message) {
    setToast(message);
    window.setTimeout(() => setToast(""), 3200);
  }

  function openStructureModal(kind) {
    setModal({ mode: "structure", kind, name: "", error: "" });
  }

  function openActionModal() {
    const action = selectedDefinition.action && typeof selectedDefinition.action === "object" ? selectedDefinition.action : {};
    setModal({
      mode: "action",
      actionType: action.type || "navigate",
      targetScene: action.scene || scene,
      targetPage: action.page || page,
      keys: Array.isArray(action.keys) ? action.keys.join(" + ") : "",
      label: action.type === "hotkey" ? hotkeyDisplayLabel(action) : "",
      fontSize: action.type === "hotkey" ? String(action.font_size ?? 16) : "16",
      command: action.command || "",
      error: "",
    });
  }

  function updateModal(field, value) {
    setModal((current) => current ? { ...current, [field]: value, error: "" } : current);
  }

  async function saveStructure() {
    if (!modal || modal.mode !== "structure") return;
    const label = modal.kind === "scene" ? "scene" : "page";
    const name = modal.name.trim();
    if (!name) {
      updateModal("error", `Enter a name for the new ${label}.`);
      return;
    }
    try {
      const response = await fetch(`/api/profiles/${encode(profileName)}/structure`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: modal.kind, name, parent: modal.kind === "page" ? scene : undefined }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `Could not add ${label}`);
      setProfile(result);
      if (modal.kind === "scene") setScene(name);
      if (modal.kind === "page") setPage(name);
      setModal(null);
      notify(`${label[0].toUpperCase()}${label.slice(1)} created`);
    } catch (error) {
      updateModal("error", error.message);
    }
  }

  async function saveAction() {
    if (!modal || modal.mode !== "action" || !profile || !profileName) return;
    let action;
    if (modal.actionType === "navigate") {
      if (!modal.targetScene || !modal.targetPage) {
        updateModal("error", "Choose a destination scene and page.");
        return;
      }
      action = { type: "navigate", scene: modal.targetScene, page: modal.targetPage };
    } else if (modal.actionType === "hotkey") {
      const keys = modal.keys.split(/\s*\+\s*|\s*,\s*/).map((key) => key.trim()).filter(Boolean);
      if (!keys.length) {
        updateModal("error", "Enter at least one key, such as CTRL + SHIFT + F1.");
        return;
      }
      const fontSize = Math.max(8, Math.min(28, Number.parseInt(modal.fontSize, 10) || 16));
      action = { type: "hotkey", keys, font_size: fontSize, ...(modal.label.trim() ? { label: modal.label.trim() } : {}) };
    } else if (modal.actionType === "launch") {
      if (!modal.command.trim()) {
        updateModal("error", "Enter a command to launch.");
        return;
      }
      action = { type: "launch", command: modal.command.trim() };
    }

    const nextProfile = JSON.parse(JSON.stringify(profile));
    const selected = selectedPage(nextProfile, scene, page);
    selected.keys = selected.keys && typeof selected.keys === "object" ? selected.keys : {};
    const keyName = String(selectedKey);
    const existing = selected.keys[keyName] && typeof selected.keys[keyName] === "object" ? selected.keys[keyName] : {};
    if (modal.actionType === "none") {
      if (Object.keys(existing).length) {
        const { action: ignoredAction, ...withoutAction } = existing;
        selected.keys[keyName] = withoutAction;
      }
    } else {
      selected.keys[keyName] = { ...existing, action };
    }

    try {
      const response = await fetch(`/api/profiles/${encode(profileName)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(nextProfile) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not save key action");
      setProfile(result);
      setModal(null);
      notify(`Action saved for key ${String(selectedKey).padStart(2, "0")}`);
    } catch (error) {
      updateModal("error", error.message);
    }
  }

  async function deleteAction() {
    if (!profile || !profileName || !selectedDefinition.action) return;
    const nextProfile = JSON.parse(JSON.stringify(profile));
    const selected = selectedPage(nextProfile, scene, page);
    selected.keys = selected.keys && typeof selected.keys === "object" ? selected.keys : {};
    const keyName = String(selectedKey);
    const existing = selected.keys[keyName];
    if (!existing || typeof existing !== "object" || !existing.action) return;

    const { action: ignoredAction, ...withoutAction } = existing;
    if (Object.keys(withoutAction).length) selected.keys[keyName] = withoutAction;
    else delete selected.keys[keyName];

    try {
      const response = await fetch(`/api/profiles/${encode(profileName)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(nextProfile) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not delete key action");
      setProfile(result);
      notify(`Action deleted for key ${String(selectedKey).padStart(2, "0")}`);
    } catch (error) {
      notify(error.message);
    }
  }

  async function applyPage() {
    setBusy(true);
    try {
      const response = await fetch(`/api/profiles/${encode(profileName)}/apply`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scene, page }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not apply page");
      notify(`Applied ${result.applied} LCD item${result.applied === 1 ? "" : "s"} to the D6`);
      refreshState().catch(() => {});
    } catch (error) {
      notify(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function refreshD6() {
    setRefreshBusy(true);
    try {
      const response = await fetch("/api/device/refresh", { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not refresh the D6");
      notify("Refresh signal sent to the D6");
      refreshState().catch(() => {});
    } catch (error) {
      notify(error.message);
    } finally {
      setRefreshBusy(false);
    }
  }

  async function updateBrightness(value) {
    setBrightness(value);
    try {
      const response = await fetch("/api/device/brightness", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not set brightness");
    } catch (error) {
      notify(error.message);
    }
  }

  async function uploadArtwork(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !profileName) return;
    try {
      const response = await fetch(`/api/profiles/${encode(profileName)}/scenes/${encode(scene)}/pages/${encode(page)}/keys/${selectedKey}/image`, { method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not upload artwork");
      setProfile(result.profile);
      notify(`Artwork saved for key ${selectedKey}`);
    } catch (error) {
      notify(error.message);
    }
  }

  async function addStructure(kind) {
    openStructureModal(kind);
  }

  const connected = Boolean(state?.device?.connected);
  const hasAssignedAction = Boolean(selectedDefinition?.action && typeof selectedDefinition.action === "object");
  const imageForKey = (key) => keyDefinition(profile, scene, page, key).image ? `/api/profiles/${encode(profileName)}/scenes/${encode(scene)}/pages/${encode(page)}/keys/${key}/image?v=${encode(keyDefinition(profile, scene, page, key).image)}` : "";

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand"><span className="brand-mark"><Icon name="bolt" size={17} /></span><span>D6 Controller</span><span className="local-tag">LOCAL</span></div>
        <div className="connection"><span className={`connection-dot ${connected ? "online" : "offline"}`} />{connected ? "Connected" : "Waiting for D6"}<span className="service-label">localhost service</span></div>
      </header>
      <div className="layout">
        <aside className="sidebar">
          <section className="side-section">
            <div className="side-heading"><span>Profiles</span><button className="icon-button" title="Profiles are stored by the local service"><Icon name="folder" /></button></div>
            <div className="side-list">
              {(state?.profiles || []).map((name) => <button className={`side-row ${name === profileName ? "active" : ""}`} key={name} onClick={() => setProfileName(name)}><Icon name="folder" size={17} /><span>{name}</span><Icon name="more" size={17} /></button>)}
            </div>
          </section>
          <section className="side-section">
            <div className="side-heading"><span>Scenes</span><button className="icon-button" onClick={() => addStructure("scene")} title="Add scene"><Icon name="plus" /></button></div>
            <div className="side-list">{scenes.map((name) => <button className={`side-row ${name === scene ? "active" : ""}`} key={name} onClick={() => setScene(name)}><Icon name="grid" size={17} /><span>{name}</span><Icon name="more" size={17} /></button>)}</div>
          </section>
          <section className="side-section pages-section">
            <div className="side-heading"><span>Pages</span><button className="icon-button" onClick={() => addStructure("page")} title="Add page"><Icon name="plus" /></button></div>
            <div className="side-list">{pages.map((name) => <button className={`side-row ${name === page ? "active" : ""}`} key={name} onClick={() => setPage(name)}><Icon name="folder" size={17} /><span>{name}</span><Icon name="more" size={17} /></button>)}</div>
          </section>
          <div className="sidebar-footer"><span className="footer-dot" />Vendor Control Deck remains installed as fallback.</div>
        </aside>

        <main className="workspace">
          <div className="workspace-header">
            <div className="selector-group"><label htmlFor="scene">Scenes</label><div className="select-wrap"><select id="scene" value={scene} onChange={(event) => setScene(event.target.value)}>{scenes.map((name) => <option key={name}>{name}</option>)}</select><Icon name="chevron" size={17} /></div></div>
            <div className="selector-group"><label htmlFor="page">Pages</label><div className="select-wrap"><select id="page" value={page} onChange={(event) => setPage(event.target.value)}>{pages.map((name) => <option key={name}>{name}</option>)}</select><Icon name="chevron" size={17} /></div></div>
            <button className="primary-button" onClick={applyPage} disabled={busy || refreshBusy || !profileName}><Icon name="download" />{busy ? "Applying…" : "Apply selected page"}</button><button className="secondary-button" onClick={refreshD6} disabled={busy || refreshBusy || !connected}><Icon name="refresh" />{refreshBusy ? "Refreshing…" : "Refresh D6"}</button>
          </div>

          <section className="deck-frame" aria-label="D6 button layout">
            <div className="deck-grid">{KEY_LAYOUT.map((key) => {
              const definition = keyDefinition(profile, scene, page, key);
              const action = definition.action;
              const actionLabel = actionDisplayLabel(definition);
              const image = action?.type === "hotkey" ? "" : imageForKey(key);
              const hasActionArt = action?.type === "hotkey";
              const actionFontSize = hasActionArt ? Math.max(8, Math.min(28, Number(action.font_size) || 16)) : undefined;
              return <button className={`deck-key ${selectedKey === key ? "selected" : ""} ${image || hasActionArt ? "has-image" : "empty"}`} key={key} onClick={() => setSelectedKey(key)} aria-label={`Key ${key}`}>
                {image ? <img src={image} alt="" /> : hasActionArt ? <span className="action-art" style={{ fontSize: `${actionFontSize}px` }}>{actionLabel}</span> : <span className="empty-art"><Icon name="plus" size={22} /><span>Add artwork</span></span>}
                <span className="key-overlay"><strong>{String(key).padStart(2, "0")}</strong><span>{actionLabel}</span></span>
              </button>;
            })}</div>
          </section>

          <section className="control-strip">
            <div className="brightness-control"><div className="control-label"><Icon name="sun" size={19} /><span>Brightness</span><strong>{brightness}%</strong></div><input aria-label="Brightness" type="range" min="0" max="100" value={brightness} onChange={(event) => setBrightness(Number(event.target.value))} onMouseUp={(event) => updateBrightness(Number(event.target.value))} onTouchEnd={(event) => updateBrightness(Number(event.target.value))} /></div>
            <div className="capability-note"><Icon name="warning" size={19} /><span>RGB unsupported on this D6</span></div>
          </section>

          <section className="key-inspector">
            <div><span className="eyebrow">Selected key</span><h2>Key {String(selectedKey).padStart(2, "0")}</h2><p>{selectedDefinition.action?.type ? `Action: ${selectedDefinition.action.type}` : "No action configured yet."}</p></div>
            <div className="inspector-actions"><input ref={fileInput} type="file" accept="image/*" onChange={uploadArtwork} hidden /><div className="inspector-buttons"><button className="secondary-button" onClick={openActionModal}><Icon name="edit" />Edit action</button><button className="secondary-button danger-button" type="button" onClick={deleteAction} disabled={!hasAssignedAction} title={hasAssignedAction ? "Delete this action" : "No action to delete"}><Icon name="trash" />Delete</button><button className="secondary-button" onClick={() => fileInput.current?.click()}><Icon name="upload" />Upload artwork</button></div><span>Images are fitted to the D6's 100×100 LCD area when applied.</span></div>
          </section>
        </main>

        <aside className="events-panel">
          <div className="events-heading"><div><h2>Live events</h2><span>Press and release reports from the D6</span></div><button className="secondary-button compact" onClick={() => setEvents([])}>Clear</button></div>
          <div className="event-list" ref={eventRail}>{events.length ? events.map((event, index) => <div className={`event-row ${event.pressed === false ? "release" : "press"}`} key={`${event.timestamp}-${index}`}><span className="event-symbol"><Icon name={event.pressed === false ? "play" : "pause"} size={13} /></span><div className="event-copy"><strong>{event.type === "key" ? (event.pressed ? "Press" : "Release") : event.type === "refresh" ? "Refresh" : "Applied"}</strong><span>{formatEvent(event)}</span></div><time>{formatTime(event.timestamp)}</time></div>) : <div className="empty-events"><Icon name="bolt" size={24} /><p>Press a D6 button to see it here.</p></div>}</div>
          <div className="events-footer"><button className={`toggle-button ${autoScroll ? "on" : ""}`} onClick={() => setAutoScroll((value) => !value)}><span><Icon name={autoScroll ? "check" : "pause"} size={14} /></span></button><span>Auto-scroll</span></div>
        </aside>
      </div>
      {modal && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setModal(null); }}>
        <section className="modal-dialog" role="dialog" aria-modal="true" aria-labelledby="modal-title" onMouseDown={(event) => event.stopPropagation()}>
          {modal.mode === "structure" ? <form onSubmit={(event) => { event.preventDefault(); saveStructure(); }}>
            <div className="modal-heading"><div><span className="eyebrow">Add {modal.kind}</span><h2 id="modal-title">Create {modal.kind}</h2></div><button type="button" className="icon-button" aria-label="Close dialog" onClick={() => setModal(null)}>×</button></div>
            <label className="modal-field"><span>{modal.kind === "scene" ? "Scene name" : "Page name"}</span><input autoFocus value={modal.name} onChange={(event) => updateModal("name", event.target.value)} placeholder={modal.kind === "scene" ? "Password Entry" : "Accounts"} /></label>
            {modal.kind === "page" && <p className="modal-hint">This page will be added under scene <strong>{scene}</strong>.</p>}
            {modal.error && <p className="modal-error">{modal.error}</p>}
            <div className="modal-actions"><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button type="submit" className="primary-button">Create {modal.kind}</button></div>
          </form> : <form onSubmit={(event) => { event.preventDefault(); saveAction(); }}>
            <div className="modal-heading"><div><span className="eyebrow">Selected key</span><h2 id="modal-title">Configure Key {String(selectedKey).padStart(2, "0")}</h2></div><button type="button" className="icon-button" aria-label="Close dialog" onClick={() => setModal(null)}>×</button></div>
            <label className="modal-field"><span>Action</span><select value={modal.actionType} onChange={(event) => updateModal("actionType", event.target.value)}><option value="navigate">Navigate to scene/page</option><option value="hotkey">Send hotkey</option><option value="launch">Launch command</option><option value="none">No action</option></select></label>
            {modal.actionType === "navigate" && <div className="modal-two-column"><label className="modal-field"><span>Scene</span><select value={modal.targetScene} onChange={(event) => setModal((current) => current ? { ...current, targetScene: event.target.value, targetPage: pageList(profile, event.target.value)[0], error: "" } : current)}>{scenes.map((name) => <option key={name}>{name}</option>)}</select></label><label className="modal-field"><span>Page</span><select value={modal.targetPage} onChange={(event) => updateModal("targetPage", event.target.value)}>{pageList(profile, modal.targetScene).map((name) => <option key={name}>{name}</option>)}</select></label></div>}
            {modal.actionType === "hotkey" && <><label className="modal-field"><span>Keys</span><input autoFocus value={modal.keys} onChange={(event) => updateModal("keys", event.target.value)} placeholder="CTRL + SHIFT + F1 or password text" /></label><label className="modal-field"><span>LCD label</span><input value={modal.label} onChange={(event) => updateModal("label", event.target.value)} placeholder="Password or account name" /></label><label className="modal-field"><span>LCD font size (px)</span><input type="number" min="8" max="28" step="1" value={modal.fontSize} onChange={(event) => updateModal("fontSize", event.target.value)} /></label></>}
            {modal.actionType === "launch" && <label className="modal-field"><span>Command</span><input autoFocus value={modal.command} onChange={(event) => updateModal("command", event.target.value)} placeholder="notepad.exe" /></label>}
            <p className="modal-hint">The action is saved in the local profile. LCD artwork is sent when you apply the selected page.</p>
            {modal.error && <p className="modal-error">{modal.error}</p>}
            <div className="modal-actions"><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button type="submit" className="primary-button">Save action</button></div>
          </form>}
        </section>
      </div>}
      {toast && <div className="toast"><Icon name="check" size={16} />{toast}</div>}
    </div>
  );
}

createRoot(document.getElementById("root")).render(<StrictMode><App /></StrictMode>);
