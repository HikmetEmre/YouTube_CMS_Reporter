import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api } from "./api";
import { MonthPage } from "./Month";
import { AlertsPage, ChannelsPage, ClientPage, ClientsPage } from "./Pages";
import "./styles.css";
import { Button, Empty, Icon, Loading, Modal, useData } from "./ui";

const NAV = [
  { path: "month", label: "This month", icon: "calendar" },
  { path: "clients", label: "Clients", icon: "users" },
  { path: "channels", label: "Channels", icon: "tv" },
  { path: "alerts", label: "Alerts", icon: "bell" },
];

function useRoute() {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => { setRoute(read()); window.scrollTo({ top: 0 }); };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

function remember(key: string, value?: string) {
  try {
    if (value === undefined) return window.localStorage.getItem(key);
    window.localStorage.setItem(key, value);
  } catch {
    /* storage can be unavailable; the app works without it */
  }
  return null;
}

function Intro({ network, onClose }: { network: string; onClose: () => void }) {
  return (
    <Modal title="Welcome to CMS Reporter" onClose={onClose}>
      <div className="intro">
        <p>
          Every month a YouTube network downloads revenue reports, works out which video or song belongs to which
          client, takes off tax and its commission, and sends each client a statement. This tool does that in four
          steps.
        </p>
        <ol>
          <li><strong>Add files</strong> exported from YouTube CMS.</li>
          <li><strong>Match assets</strong> that nobody has labelled yet.</li>
          <li><strong>Review</strong> what each client is owed.</li>
          <li><strong>Send</strong> a password-protected report to each client.</li>
        </ol>
        <p>
          You are looking at <strong>{network}</strong>, a made-up network with six months of made-up data. Explore
          freely, or add your own CMS exports: they stay in a private workspace that only you can see and are deleted
          after two hours.
        </p>
        <Button kind="primary" onClick={onClose}>Start exploring</Button>
      </div>
    </Modal>
  );
}

function App() {
  const route = useRoute();
  const { data: meta, error, reload } = useData(() => api.meta(), []);
  const [period, setPeriod] = useState<string | null>(null);
  const [toasts, setToasts] = useState<{ id: number; message: string; kind: string }[]>([]);
  const [adding, setAdding] = useState(false);
  const [newMonth, setNewMonth] = useState("");
  const [intro, setIntro] = useState(false);

  const toast = useCallback((message: string, kind: "good" | "bad" = "good") => {
    const id = Date.now() + Math.random();
    setToasts((list) => [...list, { id, message, kind }]);
    setTimeout(() => setToasts((list) => list.filter((item) => item.id !== id)), kind === "bad" ? 7000 : 3500);
  }, []);

  useEffect(() => {
    if (!meta) return;
    if (!period || !meta.periods.some((item) => item.period === period)) setPeriod(meta.periods[0]?.period ?? null);
    if (meta.demo && !remember("cms-intro-seen")) setIntro(true);
  }, [meta, period]);

  if (error) return <div className="fatal"><Empty title="CMS Reporter could not start">{error}</Empty></div>;
  if (!meta) return <div className="fatal"><Loading /></div>;

  const page = route[0] ?? "month";
  const label = meta.periods.find((item) => item.period === period)?.label ?? "";
  const create = async () => {
    try {
      const created = await api.createMonth(newMonth);
      await reload();
      setPeriod(created.period);
      setAdding(false);
      window.location.hash = "#/month";
    } catch (problem) {
      toast((problem as Error).message, "bad");
    }
  };

  let content;
  if (!period) content = <Empty title="No months yet">Create your first month to get started.</Empty>;
  else if (page === "clients" && route[1]) content = <ClientPage period={period} label={label} slug={route[1]} meta={meta} />;
  else if (page === "clients") content = <ClientsPage period={period} label={label} />;
  else if (page === "channels") content = <ChannelsPage toast={toast} meta={meta} />;
  else if (page === "alerts") content = <AlertsPage toast={toast} meta={meta} />;
  else content = <MonthPage meta={meta} period={period} step={route[1]} toast={toast} reloadMeta={reload} />;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark"><svg viewBox="0 0 32 32" width="28" height="28"><rect width="32" height="32" rx="8" /><path d="M9 22V14M16 22V9M23 22v-5" /></svg></span>
          <div>
            <strong>CMS Reporter</strong>
            <small>{meta.network}</small>
          </div>
        </div>
        <label className="month-pick">
          Month
          <select value={period ?? ""} onChange={(event) => { setPeriod(event.target.value); if (page === "month") window.location.hash = "#/month"; }}>
            {meta.periods.map((item) => <option key={item.period} value={item.period}>{item.label}</option>)}
          </select>
        </label>
        <button type="button" className="link new-month" onClick={() => setAdding(true)}><Icon name="plus" size={15} />Start a new month</button>
        <nav>
          {NAV.map((item) => (
            <a key={item.path} href={`#/${item.path}`} className={page === item.path ? "nav-on" : ""} aria-current={page === item.path ? "page" : undefined}>
              <Icon name={item.icon} />{item.label}
            </a>
          ))}
        </nav>
        {meta.demo && (
          <div className="demo-box">
            <strong>Demo</strong>
            <p>Made-up data. Your changes are private and reset after two hours.</p>
            <button type="button" className="link" onClick={() => setIntro(true)}>What is this?</button>
            <button type="button" className="link" onClick={() => api.resetDemo().then(() => { toast("Demo reset."); window.location.reload(); })}>Reset the demo</button>
          </div>
        )}
      </aside>
      <main className="main">{content}</main>

      {adding && (
        <Modal title="Start a new month" onClose={() => setAdding(false)}>
          <div className="intro">
            <label>
              Which month?
              <input type="month" value={newMonth} onChange={(event) => setNewMonth(event.target.value)} autoFocus />
            </label>
            <Button kind="primary" disabled={!newMonth} onClick={create}>Create month</Button>
          </div>
        </Modal>
      )}
      {intro && <Intro network={meta.network} onClose={() => { remember("cms-intro-seen", "1"); setIntro(false); }} />}
      <div className="toasts" aria-live="polite">
        {toasts.map((item) => (
          <div key={item.id} className={`toast toast-${item.kind}`}><Icon name={item.kind === "bad" ? "alert" : "check"} size={16} />{item.message}</div>
        ))}
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
