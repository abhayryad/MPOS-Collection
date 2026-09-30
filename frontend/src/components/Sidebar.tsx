import { useEffect, useState } from "react";
import type { User } from "../lib/api";

export interface NavItem<R extends string> {
  route: R; // for a group: its first sub-tab
  label: string;
  icon: string;
  children?: { route: R; label: string }[]; // sub-tabs
}

interface Props<R extends string> {
  items: NavItem<R>[];
  active: R;
  onToggleTheme: () => void;
  user: User;
  onLogout: () => void;
  onChangePassword?: () => void; // not offered to the built-in admin (its password is in .env)
}

export function Sidebar<R extends string>({ items, active, onToggleTheme, user, onLogout, onChangePassword }: Props<R>) {
  // Groups (e.g. Reports) are collapsed until clicked, and open while one of their sub-tabs is showing.
  const [open, setOpen] = useState<Record<string, boolean>>({});
  useEffect(() => {
    const current = items.find((i) => i.children?.some((c) => c.route === active));
    if (current) setOpen((o) => ({ ...o, [current.label]: true }));
    // only when the page changes, so a group can still be collapsed while one of its sub-tabs is open
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          V2
        </span>
        <span className="brand-name">MPOS Collection</span>
      </div>
      <nav className="nav" aria-label="Sections">
        {items.map((item) => {
          const inGroup = item.children?.some((c) => c.route === active) ?? false;
          const isOpen = !!open[item.label];
          const subId = `nav-sub-${item.label.toLowerCase().replace(/\W+/g, "-")}`;
          return (
            <div key={item.label} className="nav-group">
              {item.children ? (
                <button
                  type="button"
                  className={`nav-item nav-toggle${inGroup ? " nav-parent-active" : ""}`}
                  aria-expanded={isOpen}
                  aria-controls={subId}
                  onClick={() => setOpen((o) => ({ ...o, [item.label]: !isOpen }))}
                >
                  <span className="nav-icon" aria-hidden="true">
                    {item.icon}
                  </span>
                  <span>{item.label}</span>
                  <span className="nav-chevron" aria-hidden="true">
                    {isOpen ? "▾" : "▸"}
                  </span>
                </button>
              ) : (
                <a
                  href={`#${item.route}`}
                  className="nav-item"
                  aria-current={item.route === active ? "page" : undefined}
                >
                  <span className="nav-icon" aria-hidden="true">
                    {item.icon}
                  </span>
                  <span>{item.label}</span>
                </a>
              )}
              {item.children && isOpen && (
                <div className="nav-sub" id={subId} role="group" aria-label={`${item.label} sub-tabs`}>
                  {item.children.map((c) => (
                    <a
                      key={c.route}
                      href={`#${c.route}`}
                      className="nav-item nav-subitem"
                      aria-current={c.route === active ? "page" : undefined}
                    >
                      {c.label}
                    </a>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </nav>
      <div className="user-box">
        <div className="user-id">
          <span className="avatar" aria-hidden="true">
            {user.full_name.trim().charAt(0).toUpperCase() || "?"}
          </span>
          <span className="user-text">
            <span className="user-name">{user.full_name}</span>
            <span className="user-login">
              {user.username}
              {user.is_admin && user.username !== "admin" && " · admin"}
              {" · "}
              {user.locations.includes("HO") ? "HO" : user.locations.join(", ") || "no store"}
            </span>
          </span>
        </div>
        <div className="user-actions">
          {onChangePassword && (
            <button type="button" className="ghost" onClick={onChangePassword}>
              Password
            </button>
          )}
          <button type="button" className="ghost" onClick={onToggleTheme} title="Toggle light / dark">
            ◐
          </button>
          <button type="button" className="ghost" onClick={onLogout}>
            Log out
          </button>
        </div>
      </div>
    </aside>
  );
}
