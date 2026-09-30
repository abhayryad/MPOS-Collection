import { useEffect, useState } from "react";
import { api, ApiError, LOGGED_OUT_EVENT, type Me } from "./lib/api";
import { useAsync, useHashRoute, useTheme } from "./lib/hooks";
import { Sidebar, type NavItem } from "./components/Sidebar";
import { SalePostingDownload } from "./pages/SalePostingDownload";
import { ElectronicGeneral } from "./pages/reports/ElectronicGeneral";
import { SaleByHour } from "./pages/reports/SaleByHour";
import { DailySalesSummary } from "./pages/reports/DailySalesSummary";
import { Admin } from "./pages/Admin";
import { ChangePassword, Login } from "./pages/Login";

const ROUTES = ["/downloads", "/reports/electronic-general", "/reports/sale-by-hour", "/reports/daily-sales-summary", "/admin"] as const;
type Route = (typeof ROUTES)[number];

/** Tabs and the role that opens each (null = admin only). Mirrors backend/mpos/auth/roles.py.
 *  A group's route is its first sub-tab; add a report by adding a child here and a route above. */
const NAV: (NavItem<Route> & { role: string | null })[] = [
  { route: "/downloads", label: "Sale Posting Download", icon: "↓", role: "SALE_POSTING" },
  {
    route: "/reports/electronic-general",
    label: "Reports",
    icon: "▦",
    role: "REPORTS",
    children: [
      { route: "/reports/electronic-general", label: "Electronic General" },
      { route: "/reports/sale-by-hour", label: "Store Sale by Hour" },
      { route: "/reports/daily-sales-summary", label: "Daily Sales Summary" },
    ],
  },
  { route: "/admin", label: "Admin", icon: "⚙", role: null },
];

/** Old links that now live elsewhere. */
const REDIRECTS: Record<string, Route> = { "/reports": "/reports/electronic-general" };

type Session = { state: "loading" } | { state: "out" } | { state: "in"; me: Me } | { state: "error"; message: string };

export default function App() {
  const [session, setSession] = useState<Session>({ state: "loading" });
  const [changingPassword, setChangingPassword] = useState(false);

  const checkSession = () =>
    api
      .me()
      .then((me) => setSession({ state: "in", me }))
      .catch((e: ApiError) =>
        setSession(e.status === 401 ? { state: "out" } : { state: "error", message: e.message }),
      );

  useEffect(() => {
    checkSession();
    const onLoggedOut = () => setSession({ state: "out" });
    window.addEventListener(LOGGED_OUT_EVENT, onLoggedOut);
    return () => window.removeEventListener(LOGGED_OUT_EVENT, onLoggedOut);
  }, []);

  if (session.state === "loading") return <div className="auth-screen hint">Loading…</div>;
  if (session.state === "error")
    return (
      <div className="auth-screen">
        <div className="auth-card">
          <div className="err">{session.message}</div>
          <button type="button" onClick={checkSession}>
            Try again
          </button>
        </div>
      </div>
    );
  if (session.state === "out") return <Login onLogin={(me) => setSession({ state: "in", me })} />;

  const me = session.me;
  const logout = async () => {
    await api.logout().catch(() => undefined);
    setChangingPassword(false);
    setSession({ state: "out" });
  };

  if (me.must_change_password || changingPassword)
    return (
      <ChangePassword
        me={me}
        forced={me.must_change_password}
        onDone={(updated) => {
          setChangingPassword(false);
          setSession({ state: "in", me: updated });
        }}
        onCancel={me.must_change_password ? undefined : () => setChangingPassword(false)}
        onSignOut={me.must_change_password ? logout : undefined}
      />
    );
  return <Shell me={me} onLogout={logout} onChangePassword={() => setChangingPassword(true)} />;
}

function Shell({ me, onLogout, onChangePassword }: { me: Me; onLogout: () => void; onChangePassword: () => void }) {
  const nav = NAV.filter((n) => (n.role === null ? me.is_admin : me.is_admin || me.roles.includes(n.role)));
  const routes = nav.flatMap((n) => (n.children ? n.children.map((c) => c.route) : [n.route]));
  useEffect(() => {
    const target = REDIRECTS[window.location.hash.replace(/^#/, "")];
    if (target) window.location.replace(`#${target}`);
  }, []);
  const route = useHashRoute(routes.length ? routes : ROUTES, routes[0] ?? "/downloads");
  const toggleTheme = useTheme();
  // Store list, date span and MOP codes are shared by the data pages.
  const meta = useAsync((signal) => api.meta(signal), []);

  return (
    <div className="shell">
      <Sidebar
        items={nav}
        active={route}
        onToggleTheme={toggleTheme}
        user={me}
        onLogout={onLogout}
        onChangePassword={me.is_admin ? undefined : onChangePassword}
      />
      <main className="main">
        <div className="wrap">
          {nav.length === 0 && (
            <section className="card placeholder">
              <h2>No access yet</h2>
              <p className="hint">Your account has no tabs assigned. Ask your admin to give you a role.</p>
            </section>
          )}
          {route === "/downloads" && routes.includes(route) && <SalePostingDownload meta={meta} />}
          {route === "/reports/electronic-general" && routes.includes(route) && <ElectronicGeneral meta={meta} />}
          {route === "/reports/sale-by-hour" && routes.includes(route) && <SaleByHour meta={meta} />}
          {route === "/reports/daily-sales-summary" && routes.includes(route) && <DailySalesSummary />}
          {route === "/admin" && me.is_admin && <Admin me={me} />}
        </div>
      </main>
    </div>
  );
}
