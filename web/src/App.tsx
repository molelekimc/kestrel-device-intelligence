import { BrowserRouter, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { FreshnessBar } from "./components/FreshnessBar";
import { LiveProvider, useLive } from "./live";
import Ask from "./pages/Ask";
import ControlRoom from "./pages/ControlRoom";
import TV from "./pages/TV";
import DataTrust from "./pages/DataTrust";
import Overview from "./pages/Overview";
import Remediation from "./pages/Remediation";
import ServiceDesk from "./pages/ServiceDesk";
import Why from "./pages/Why";

const NAV = [
  ["/", "Control room"], ["/detail", "Detail"], ["/why", "Why"], ["/remediation", "What's being done"],
  ["/service-desk", "Service desk"], ["/data", "Data trust"], ["/ask", "Ask the fleet"],
] as const;

function WeekPicker() {
  const { status, week, setWeek } = useLive();
  if (!status?.weeks.length) return null;
  return (
    <label className="week">
      Week{" "}
      <select value={week ?? ""} onChange={(e) => setWeek(e.target.value)}>
        {status.weeks.map((w) => <option key={w} value={w}>{w}</option>)}
      </select>
    </label>
  );
}

function Shell() {
  // The control room is a full-screen view with its own header; the detail pages share this shell.
  const path = useLocation().pathname;
  if (path === "/") return <ControlRoom />;
  if (path === "/tv") return <TV />;
  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <span className="eyebrow">Kestrel Assembly Group · Windows fleet</span>
          <strong>Fleet reliability and business impact</strong>
          <span className="disclaimer">Fictional client, synthetic data (Ample Roots Digital case study). Costs are illustrative.</span>
        </div>
        <WeekPicker />
      </header>
      <nav className="nav" aria-label="Pages">
        {NAV.map(([to, label]) => (
          <NavLink key={to} to={to} end={to === "/"}>{label}</NavLink>
        ))}
      </nav>
      <FreshnessBar />
      <main>
        <Routes>
          <Route path="/detail" element={<Overview />} />
          <Route path="/why" element={<Why />} />
          <Route path="/remediation" element={<Remediation />} />
          <Route path="/service-desk" element={<ServiceDesk />} />
          <Route path="/data" element={<DataTrust />} />
          <Route path="/ask" element={<Ask />} />
          <Route path="*" element={<Overview />} />
        </Routes>
      </main>
      <footer className="foot">
        Every number comes from the governed fleet tools: aggregates only, groups under 10 hidden, no device or person
        identifiers. Read-only.
      </footer>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <LiveProvider>
        <Shell />
      </LiveProvider>
    </BrowserRouter>
  );
}
