/** 复制按钮。antd 的 Typography 已经带了剪贴板与"已复制"反馈, 这里只挑图标与文案。
 *
 * 颜色是浅灰的 (见 styles/index.css 的 .forge-copy): 复制是随手用一下的东西,
 * 不该比它旁边的正文还显眼。
 */

import { Typography } from "antd";
import { CheckOutlined, CopyOutlined } from "@ant-design/icons";

export function CopyButton({
  content,
  compact = false,
  label = "复制",
}: {
  content: string;
  /** 只留图标, 不带文字。放在代码块标题这类窄条里用。 */
  compact?: boolean;
  label?: string;
}) {
  return (
    <Typography.Text
      className="forge-copy"
      copyable={{
        text: content,
        tooltips: [label, "已复制"],
        icon: [
          <span key="copy">
            <CopyOutlined />
            {!compact && ` ${label}`}
          </span>,
          <span key="copied">
            <CheckOutlined />
            {!compact && " 已复制"}
          </span>,
        ],
      }}
    />
  );
}
