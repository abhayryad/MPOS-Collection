import { useEffect, useId, useRef, useState } from "react";

interface Props {
  stores: string[];
  value: string; // "" = all stores
  onChange: (store: string) => void;
}

/** Type-to-search store picker. Enter/click picks a match; Esc or blur restores the current store. */
export function StoreSearch({ stores, value, onChange }: Props) {
  const [query, setQuery] = useState(value);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => setQuery(value), [value]);

  const q = query.trim().toUpperCase();
  const typing = q !== value.toUpperCase();
  const matches = typing && q ? stores.filter((s) => s.toUpperCase().includes(q)) : stores;
  const options = ["", ...matches]; // "" = All stores

  const pick = (store: string) => {
    onChange(store);
    setQuery(store);
    setOpen(false);
  };
  const close = () => {
    setQuery(value);
    setOpen(false);
  };

  return (
    <div
      className="store-search"
      ref={box}
      onBlur={(e) => !box.current?.contains(e.relatedTarget as Node) && close()}
    >
      <input
        type="search"
        role="combobox"
        aria-label="Search store"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={open ? `${listId}-${active}` : undefined}
        placeholder="All stores"
        value={query}
        onFocus={(e) => {
          e.target.select();
          setOpen(true);
          setActive(0);
        }}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
          setActive(e.target.value.trim() ? 1 : 0); // highlight the first match while typing
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
            setActive((a) => Math.min(a + 1, options.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === "Enter") {
            e.preventDefault();
            if (open && options[active] !== undefined) pick(options[active]);
          } else if (e.key === "Escape") {
            close();
            e.currentTarget.blur();
          }
        }}
      />
      {open && (
        <ul className="store-list" id={listId} role="listbox">
          {options.map((s, i) => (
            <li
              key={s || "__all"}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={s === value}
              className={i === active ? "active" : undefined}
              tabIndex={-1}
              onMouseDown={(e) => e.preventDefault()} // keep focus in the input
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(s)}
            >
              {s || "All stores"}
              {s === value && <span aria-hidden="true">✓</span>}
            </li>
          ))}
          {matches.length === 0 && <li className="store-none">No store matches “{query.trim()}”</li>}
        </ul>
      )}
    </div>
  );
}
