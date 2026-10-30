import { useLive } from "./hooks/useLive";
import { navigate, useRoute } from "./hooks/useRoute";
import { useSettings } from "./hooks/useSettings";
import { CandidateDetailPage } from "./pages/CandidateDetail";
import { CandidatesPage } from "./pages/Candidates";
import { DashboardPage } from "./pages/Dashboard";
import { HistoryPage } from "./pages/History";
import { JournalPage } from "./pages/Journal";
import { LogsPage } from "./pages/Logs";
import { SettingsPage } from "./pages/Settings";
import { StatusPage } from "./pages/Status";
import { TokenSearchPage } from "./pages/TokenSearch";
import { TradingPage } from "./pages/Trading";
import { WalletDetailPage, WalletsPage } from "./pages/Wallets";

const NAV: { id: string; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "candidates", label: "Candidates" },
  { id: "wallets", label: "Wallets" },
  { id: "search", label: "Search" },
  { id: "history", label: "History" },
  { id: "trading", label: "Trading" },
  { id: "journal", label: "Journal" },
  { id: "status", label: "Status" },
  { id: "logs", label: "Logs" },
  { id: "settings", label: "Settings" },
];

function Page(): JSX.Element {
  const route = useRoute();
  const { loaded, api } = useSettings();
  if (!loaded) return <div className="row"><span className="spinner" /> <span className="muted">Loading settings…</span></div>;
  if (!api.configured && route.path !== "settings") {
    return (
      <div className="card">
        <h2>Welcome to GemFinder AI</h2>
        <p className="small muted">Connect this side panel to your deployed Railway API to see candidates, whale activity and research. Nothing is shown until a real backend is configured - there is no demo data.</p>
        <button className="primary" onClick={() => navigate("settings")}>
          open settings
        </button>
      </div>
    );
  }
  switch (route.path) {
    case "candidates":
      return route.param ? <CandidateDetailPage mint={route.param} /> : <CandidatesPage />;
    case "wallets":
      return route.param ? <WalletDetailPage address={route.param} /> : <WalletsPage />;
    case "search":
      return <TokenSearchPage />;
    case "history":
      return <HistoryPage initialTab={route.query.get("tab") ?? undefined} />;
    case "trading":
      return <TradingPage />;
    case "journal":
      return <JournalPage />;
    case "status":
      return <StatusPage />;
    case "logs":
      return <LogsPage />;
    case "settings":
      return <SettingsPage />;
    default:
      return <DashboardPage />;
  }
}

export function App(): JSX.Element {
  const route = useRoute();
  const live = useLive();
  const active = route.path === "" ? "dashboard" : route.path;
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <img src="icons/icon48.png" alt="" />
          <h1>GemFinder AI</h1>
        </div>
        <div className="row">
          {route.param ? (
            <button className="small" onClick={() => window.history.back()}>
              ← back
            </button>
          ) : null}
          <span className={`live ${live.status}`} title={live.status === "live" ? "receiving live scanner events" : "no live connection"}>
            <span className="dot" /> {live.status === "live" ? "LIVE" : live.status}
          </span>
        </div>
      </header>
      <nav className="nav">
        {NAV.map((n) => (
          <button key={n.id} className={active === n.id ? "active" : ""} onClick={() => navigate(n.id)}>
            {n.label}
          </button>
        ))}
      </nav>
      <main className="content">
        <Page />
        <div className="footer-note">Research and decision support only. No trading, no wallet access, no guarantees.</div>
      </main>
    </div>
  );
}
