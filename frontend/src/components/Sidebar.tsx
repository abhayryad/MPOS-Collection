export interface NavItem<R extends string> {
  route: R;
  label: string;
  icon: string;
}

interface Props<R extends string> {
  items: NavItem<R>[];
  active: R;
  onToggleTheme: () => void;
}

export function Sidebar<R extends string>({ items, active, onToggleTheme }: Props<R>) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          V2
        </span>
        <span className="brand-name">POS Collection</span>
      </div>
      <nav className="nav" aria-label="Sections">
        {items.map((item) => (
          <a
            key={item.route}
            href={`#${item.route}`}
            className="nav-item"
            aria-current={item.route === active ? "page" : undefined}
          >
            <span className="nav-icon" aria-hidden="true">
              {item.icon}
            </span>
            <span>{item.label}</span>
          </a>
        ))}
      </nav>
      <button type="button" className="ghost theme-btn" onClick={onToggleTheme} title="Toggle light / dark">
        ◐ <span>Theme</span>
      </button>
    </aside>
  );
}
