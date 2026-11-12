import { ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";

export const usd = (value: number, digits = 2) =>
  value.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: digits, maximumFractionDigits: digits });

export const compactUsd = (value: number) =>
  Math.abs(value) >= 10000
    ? "$" + value.toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 1 })
    : usd(value, Math.abs(value) >= 100 ? 0 : 2);

export const local = (value: number, currency: string) =>
  `${value.toLocaleString("en-US", { maximumFractionDigits: 0 })} ${currency}`;

export const percent = (value: number) => `${(value * 100).toLocaleString("en-US", { maximumFractionDigits: 1 })}%`;

export const shortMonth = (period: string) => {
  const [year, month] = period.split("-").map(Number);
  return new Date(year, month - 1, 1).toLocaleString("en-US", { month: "short" });
};

const ICONS: Record<string, string> = {
  check: "M5 12.5l4.5 4.5L19 7.5",
  alert: "M12 8v5m0 3.5v.5M10.3 4.2L2.9 17a2 2 0 001.7 3h14.8a2 2 0 001.7-3L13.7 4.2a2 2 0 00-3.4 0z",
  upload: "M12 16V5m0 0l-4 4m4-4l4 4M5 19h14",
  download: "M12 5v11m0 0l-4-4m4 4l4-4M5 19h14",
  file: "M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5zm0 0v5h5",
  eye: "M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12zm9.5 2.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5z",
  copy: "M9 9h10v11H9zM5 15V4h10",
  x: "M6 6l12 12M18 6L6 18",
  plus: "M12 5v14M5 12h14",
  back: "M15 5l-7 7 7 7",
  up: "M12 19V6m0 0l-5 5m5-5l5 5",
  down: "M12 5v13m0 0l-5-5m5 5l5-5",
  mail: "M4 6h16v12H4zm0 0l8 7 8-7",
  refresh: "M20 11a8 8 0 10-2.3 5.7M20 5v6h-6",
  calendar: "M4 7h16v13H4zM4 11h16M8 4v4M16 4v4",
  users: "M16 19v-1.5a3.5 3.5 0 00-3.5-3.5h-5A3.5 3.5 0 004 17.5V19M10 10a3 3 0 100-6 3 3 0 000 6zm10 9v-1.5a3.5 3.5 0 00-2.5-3.3M15 4.2a3 3 0 010 5.6",
  tv: "M3 6h18v11H3zM8 21h8M10 9.5v4l4-2z",
  bell: "M6 16V11a6 6 0 0112 0v5l1.5 2h-15zM10 20a2 2 0 004 0",
};

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={ICONS[name]} />
    </svg>
  );
}

export function Button({ children, onClick, kind = "secondary", icon, busy, disabled, small, title }: {
  children: ReactNode;
  onClick?: () => void;
  kind?: "primary" | "secondary" | "ghost";
  icon?: string;
  busy?: boolean;
  disabled?: boolean;
  small?: boolean;
  title?: string;
}) {
  return (
    <button type="button" className={`btn btn-${kind}${small ? " btn-small" : ""}`} onClick={onClick} disabled={disabled || busy} title={title}>
      {busy ? <span className="spinner" /> : icon ? <Icon name={icon} size={small ? 15 : 17} /> : null}
      {children}
    </button>
  );
}

export function Status({ state, children }: { state: "good" | "warning" | "neutral"; children: ReactNode }) {
  return (
    <span className={`status status-${state}`}>
      {state !== "neutral" && <Icon name={state === "good" ? "check" : "alert"} size={14} />}
      {children}
    </span>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children && <p>{children}</p>}
    </div>
  );
}

export function Loading() {
  return (
    <div className="empty">
      <span className="spinner spinner-large" />
    </div>
  );
}

/** Load data for a view; `reload` re-fetches without flashing the loading state. */
export function useData<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loader = useRef(load);
  loader.current = load;
  const reload = () =>
    loader.current().then(
      (value) => {
        setData(value);
        setError(null);
      },
      (problem: Error) => setError(problem.message),
    );
  useEffect(() => {
    setData(null);
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, reload, setData };
}

export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    setWidth(element.clientWidth);
    const observer = new ResizeObserver(() => setWidth(element.clientWidth));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className={`modal${wide ? " modal-wide" : ""}`} role="dialog" aria-label={title} onClick={(event) => event.stopPropagation()}>
        <header>
          <h3>{title}</h3>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <Icon name="x" />
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}

export function Avatar({ name }: { name: string }) {
  const initials = name.split(/\s+/).slice(0, 2).map((word) => word[0]).join("").toUpperCase();
  return <span className="avatar" aria-hidden="true">{initials}</span>;
}
