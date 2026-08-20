/** 共享的线性图标；统一 24 视窗、无填充、跟随 currentColor。 */

type IconProps = { className?: string };

export function ChevronIcon({ className = "" }: IconProps) {
  return <svg className={`icon ${className}`} viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>;
}

export function CheckIcon({ className = "" }: IconProps) {
  return <svg className={`icon ${className}`} viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4.5 4.5L19 7" /></svg>;
}

export function PlanIcon({ className = "" }: IconProps) {
  return <svg className={`icon ${className}`} viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5h8M8 10h8M8 15h5" /><path d="M6 3h12a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z" /></svg>;
}

export function ShieldIcon({ className = "" }: IconProps) {
  return <svg className={`icon ${className}`} viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 5.5 6v5.2c0 4.2 2.6 7.7 6.5 9.8 3.9-2.1 6.5-5.6 6.5-9.8V6L12 3Z" /><path d="m9.4 12 1.7 1.7 3.7-4" /></svg>;
}

export function GearIcon({ className = "" }: IconProps) {
  return <svg className={`icon ${className}`} viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3.1" /><path d="M12 3.4v2.2M12 18.4v2.2M20.6 12h-2.2M5.6 12H3.4M18.1 5.9l-1.6 1.6M7.5 16.5l-1.6 1.6M18.1 18.1l-1.6-1.6M7.5 7.5 5.9 5.9" /></svg>;
}
