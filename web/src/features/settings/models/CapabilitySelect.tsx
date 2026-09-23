import { DownOutlined } from "@ant-design/icons";
import { Flex, Select, Typography } from "antd";

export function CapabilitySelect({
  value,
  onChange,
  disabled,
  detail,
  placeholder = "选择能力",
}: {
  value?: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  detail: string;
  placeholder?: string;
}) {
  const options = [
    { value: "true", label: "支持" },
    { value: "false", label: "不支持" },
  ];

  return (
    <Select
      className="forge-capability-select"
      classNames={{ popup: { root: "forge-capability-select-popup" } }}
      value={value}
      onChange={onChange}
      disabled={disabled}
      placeholder={placeholder}
      suffixIcon={<DownOutlined />}
      options={options}
      optionRender={(option) => (
        <Flex align="center" justify="space-between" gap={12} className="forge-capability-option">
          <span>{option.label}</span>
          <Typography.Text type="secondary" className="forge-capability-detail">
            {detail}
          </Typography.Text>
        </Flex>
      )}
    />
  );
}
