import { api } from "./lib/api";
import { useAsync, useHashRoute, useTheme } from "./lib/hooks";
import { Sidebar, type NavItem } from "./components/Sidebar";
import { SalePostingDownload } from "./pages/SalePostingDownload";
import { Reports } from "./pages/Reports";

const ROUTES = ["/downloads", "/reports"] as const;
type Route = (typeof ROUTES)[number];

const NAV: NavItem<Route>[] = [
  { route: "/downloads", label: "Sale Posting Download", icon: "↓" },
  { route: "/reports", label: "Reports", icon: "▦" },
];

export default function App() {
  const route = useHashRoute(ROUTES, "/downloads");
  const toggleTheme = useTheme();
  // Store list, date span and MOP codes are shared by every page.
  const meta = useAsync((signal) => api.meta(signal), []);

  return (
    <div className="shell">
      <Sidebar items={NAV} active={route} onToggleTheme={toggleTheme} />
      <main className="main">
        <div className="wrap">
          {route === "/downloads" && <SalePostingDownload meta={meta} />}
          {route === "/reports" && <Reports meta={meta} />}
        </div>
      </main>
    </div>
  );
}
