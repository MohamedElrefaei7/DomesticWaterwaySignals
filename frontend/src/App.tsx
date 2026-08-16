import { NavLink, Route, Routes } from "react-router-dom";
import { Health } from "./views/Health";
import { RiverMap } from "./views/RiverMap";
import { Signals } from "./views/Signals";
import { Today } from "./views/Today";

const NAV = [
  { to: "/", label: "Today", end: true },
  { to: "/river", label: "River", end: false },
  { to: "/signals", label: "Signals", end: false },
  { to: "/health", label: "Health", end: false },
];

export function App() {
  return (
    <div className="shell">
      <a className="skip" href="#main">
        Skip to content
      </a>

      <header className="masthead">
        <div className="masthead-brand">
          <span className="masthead-rule" aria-hidden="true" />
          <div>
            <p className="masthead-title display">Inland Waterway Signals</p>
            <p className="masthead-sub">
              Low water, barge rates, and what the record will and will not support
            </p>
          </div>
        </div>
        <nav className="nav" aria-label="Views">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => (isActive ? "nav-link on" : "nav-link")}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>

      <main id="main">
        <Routes>
          <Route path="/" element={<Today />} />
          <Route path="/river" element={<RiverMap />} />
          <Route path="/signals" element={<Signals />} />
          <Route path="/health" element={<Health />} />
        </Routes>
      </main>

      <footer className="footer">
        <p>
          Every number here is reproducible from a query. A refusal is an answer, a
          gap is not a zero, and a passing count is never shown without the
          denominator it came from.
        </p>
      </footer>
    </div>
  );
}
