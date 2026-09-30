const inr = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const whole = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const qty = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

export const fmtAmount = (v: number) => inr.format(v);
export const fmtMoney = (v: number) => (v < 0 ? "−₹" : "₹") + inr.format(Math.abs(v));
export const fmtQty = (v: number) => qty.format(v);

/** Compact Indian units for axis ticks and labels: 1.2k, 3.4L, 1.1Cr. */
export function fmtShort(v: number) {
  const a = Math.abs(v);
  const trim = (n: number) => n.toFixed(1).replace(/\.0$/, "");
  if (a >= 1e7) return trim(v / 1e7) + "Cr";
  if (a >= 1e5) return trim(v / 1e5) + "L";
  if (a >= 1e3) return trim(v / 1e3) + "k";
  return whole.format(v);
}

/** "2026-09-28" -> "28/09" */
export const dayLabel = (iso: string) => {
  const [, m, d] = iso.split("-");
  return `${d}/${m}`;
};

/** Categorical colour slots, assigned to entities in fixed order (see styles.css). */
export const SERIES = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7", "--s8"].map((v) => `var(${v})`);
