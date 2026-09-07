/** 顶栏的姿态选择器。两个轴各自成组, 预设只是同时设两个轴的快捷方式。 */

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckIcon, ChevronIcon } from "../../icons";
import { approvalOptions, sandboxOptions, stanceLabel } from "../../stance";
import type { Stance } from "../../stance";
import { useEscape } from "../../ui";

export function ModeMenu({ value, disabled, onChange }: { value: Stance; disabled: boolean; onChange: (patch: Partial<Stance>) => void }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  useEscape(open, useCallback(() => setOpen(false), []));
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", closeOutside);
    return () => document.removeEventListener("mousedown", closeOutside);
  }, [open]);
  // 圆点跟着隔离档走: 那是"能造成多大后果"这一问的答案, 也是用户扫一眼最需要知道的。
  return <div className="mode-menu" ref={rootRef}>
    <button type="button" className="mode-trigger" disabled={disabled} aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((state) => !state)}>
      <i className={`mode-dot ${value.sandbox}`} />
      <span>{stanceLabel(value)}</span>
      <ChevronIcon className="mode-caret" />
    </button>
    {open && <div className="mode-options">
      <StanceGroup label="隔离" hint="围栏允许什么" options={sandboxOptions} current={value.sandbox} dotted onPick={(next) => { setOpen(false); onChange({ sandbox: next }); }} />
      <StanceGroup label="审批" hint="什么时候要你点头" options={approvalOptions} current={value.approval} onPick={(next) => { setOpen(false); onChange({ approval: next }); }} />
    </div>}
  </div>;
}

export function StanceGroup({ label, hint, options, current, dotted, onPick }: { label: string; hint: string; options: Array<{ value: string; label: string; hint: string }>; current: string; dotted?: boolean; onPick: (value: string) => void }) {
  return <section className="stance-group">
    <header><strong>{label}</strong><small>{hint}</small></header>
    <ul role="listbox" aria-label={label}>
      {options.map((item) => (
        <li key={item.value}>
          <button type="button" role="option" aria-selected={item.value === current} className={item.value === current ? "active" : ""} onClick={() => { if (item.value !== current) onPick(item.value); }}>
            {dotted && <i className={`mode-dot ${item.value}`} />}
            <span><strong>{item.label}</strong><small>{item.hint}</small></span>
            {item.value === current && <CheckIcon className="mode-check" />}
          </button>
        </li>
      ))}
    </ul>
  </section>;
}
