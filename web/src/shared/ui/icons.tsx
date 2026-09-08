/** 共享的线性图标；统一 24 视窗、无填充、跟随 currentColor。 */

type IconProps = { className?: string };

export function ChevronIcon({ className = "" }: IconProps) {
  return (
    <svg className={`icon ${className}`} viewBox="0 0 24 24" aria-hidden="true">
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

export function CheckIcon({ className = "" }: IconProps) {
  return (
    <svg className={`icon ${className}`} viewBox="0 0 24 24" aria-hidden="true">
      <path d="m5 12 4.5 4.5L19 7" />
    </svg>
  );
}

export function PlanIcon({ className = "" }: IconProps) {
  return (
    <svg className={`icon ${className}`} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M8 5h8M8 10h8M8 15h5" />
      <path d="M6 3h12a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z" />
    </svg>
  );
}

export function ShieldIcon({ className = "" }: IconProps) {
  return (
    <svg className={`icon ${className}`} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 3 5.5 6v5.2c0 4.2 2.6 7.7 6.5 9.8 3.9-2.1 6.5-5.6 6.5-9.8V6L12 3Z" />
      <path d="m9.4 12 1.7 1.7 3.7-4" />
    </svg>
  );
}

export function GearIcon({ className = "" }: IconProps) {
  // 一圈齿的轮廓 + 中心圆。原先是「圆心 + 八条放射线段」，那画出来是太阳/亮度调节，
  // 不是齿轮——设置入口的图标要一眼认得出，认不出就等于没有入口。
  return (
    <svg className={`icon ${className}`} viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="3.2" />
      <path d="M19.4 14.4a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.04 1.56V21a2 2 0 1 1-4 0v-.11a1.7 1.7 0 0 0-1.1-1.56 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.56-1.04H3a2 2 0 1 1 0-4h.11a1.7 1.7 0 0 0 1.56-1.1 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34h.08a1.7 1.7 0 0 0 1.04-1.56V3a2 2 0 1 1 4 0v.11a1.7 1.7 0 0 0 1.04 1.56 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87v.08a1.7 1.7 0 0 0 1.56 1.04H21a2 2 0 1 1 0 4h-.11a1.7 1.7 0 0 0-1.49 1.04z" />
    </svg>
  );
}
