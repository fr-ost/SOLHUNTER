import { useEffect, useState } from "react";
import { useLive } from "./hooks/useLive";
import { navigate, useRoute } from "./hooks/useRoute";
import { useSettings } from "./hooks/useSettings";
import { CandidateDetailPage } from "./pages/CandidateDetail";
import { CandidatesPage } from "./pages/Candidates";
import { DashboardPage } from "./pages/Dashboard";
import { HistoryPage } from "./pages/History";
import { JournalPage } from "./pages/Journal";
import { LoginPage } from "./pages/Login";
import { LogsPage } from "./pages/Logs";
import { SettingsPage } from "./pages/Settings";
import { StatusPage } from "./pages/Status";
import { SurvivorsPage } from "./pages/Survivors";
import { TokenSearchPage } from "./pages/TokenSearch";
import { TradingPage } from "./pages/Trading";
import { WalletTrackerPage } from "./pages/WalletTracker";
import { WalletDetailPage, WalletsPage } from "./pages/Wallets";

const NAV: { id: string; label: string; icon: string }[] = [
  { id: "dashboard", label: "Dashboard", icon: "◫" },
  { id: "candidates", label: "Candidates", icon: "◆" },
  { id: "survivors", label: "Survivors", icon: "⟰" },
  { id: "tracker", label: "Wallet tracker", icon: "👁" },
  { id: "wallets", label: "Wallets", icon: "◎" },
  { id: "search", label: "Token search", icon: "⌕" },
  { id: "history", label: "History", icon: "⟲" },
  { id: "trading", label: "Paper trading", icon: "▤" },
  { id: "journal", label: "Journal", icon: "✎" },
  { id: "status", label: "Status", icon: "♥" },
  { id: "logs", label: "Logs", icon: "≡" },
  { id: "settings", label: "Settings", icon: "⚙" },
];

function Page(): JSX.Element {
  const route = useRoute();
  switch (route.path) {
    case "tracker":
      return <WalletTrackerPage />;
    case "survivors":
      return <SurvivorsPage />;
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
  const { signedIn, signOut } = useSettings();
  const [menuOpen, setMenuOpen] = useState(false);
  const active = route.path === "" ? "dashboard" : route.path;

  useEffect(() => setMenuOpen(false), [route.path, route.param]);

  if (!signedIn) return <LoginPage />;

  const liveBadge = (
    <span className={`live ${live.status}`} title={live.status === "live" ? "receiving live scanner events" : "no live connection"}>
      <span className="dot" /> {live.status === "live" ? "LIVE" : live.status}
    </span>
  );

  return (
    <div className="app">
      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <div className="brand">
          <img src="/favicon.png" alt="" />
          <h1>SolHunter</h1>
        </div>
        <nav className="nav">
          {NAV.map((n) => (
            <button key={n.id} className={active === n.id ? "active" : ""} onClick={() => navigate(n.id)}>
              <span className="icon">{n.icon}</span> {n.label}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          {liveBadge}
          <button className="small" onClick={signOut} title="forget the API key in this browser">
            sign out
          </button>
        </div>
      </aside>
      {menuOpen ? <div className="backdrop" onClick={() => setMenuOpen(false)} /> : null}
      <div className="main">
        <header className="topbar">
          <div className="row">
            <button className="small menu-btn" aria-label="menu" onClick={() => setMenuOpen((o) => !o)}>
              ☰
            </button>
            {route.param ? (
              <button className="small" onClick={() => window.history.back()}>
                ← back
              </button>
            ) : null}
            <span className="page-title">{NAV.find((n) => n.id === active)?.label ?? "Dashboard"}</span>
          </div>
          <div className="row topbar-right">{liveBadge}</div>
        </header>
        <main className="content">
          <Page />
          <div className="footer-note">SolHunter is research and decision support only. No trading, no wallet access, no guarantees.</div>
        </main>
      </div>
    </div>
  );
}
