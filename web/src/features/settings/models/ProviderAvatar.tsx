/** 供应商的首字母徽标。颜色按 id 定, 认不出的走中性灰。 */

import { Avatar } from "antd";
import { ApiOutlined } from "@ant-design/icons";

const PALETTE = ["#1677ff", "#13c2c2", "#52c41a", "#fa8c16", "#eb2f96", "#722ed1", "#fa541c"];

export function ProviderAvatar({
  providerId,
  label,
  size = "default",
}: {
  providerId: string;
  label?: string;
  size?: number | "small" | "default" | "large";
}) {
  if (providerId === "none") {
    return <Avatar shape="square" size={size} icon={<ApiOutlined />} style={{ flex: "0 0 auto" }} />;
  }
  const seed = [...providerId].reduce((sum, letter) => sum + letter.charCodeAt(0), 0);
  return (
    <Avatar
      shape="square"
      size={size}
      style={{ background: PALETTE[seed % PALETTE.length], flex: "0 0 auto" }}
    >
      {(label || providerId).slice(0, 1).toUpperCase()}
    </Avatar>
  );
}
