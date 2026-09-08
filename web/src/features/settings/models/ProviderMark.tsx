/** 供应商的首字母徽标。颜色由 .provider-<id> 决定, 认不出的用 none。 */

export function ProviderMark({ providerId, label }: { providerId: string; label?: string }) {
  return (
    <span className={`provider-mark provider-${providerId}`}>
      {(label || providerId).slice(0, 1).toUpperCase()}
    </span>
  );
}
