/**
 * 一组字段一起改、一次保存。
 *
 * 逐项保存的问题不只是点击次数: 每次保存都要重新拉一遍配置, 而重新拉配置会把同一张表单里
 * 其他还没保存的输入冲掉 —— 用户填了三格, 保存第一格, 另外两格就没了。
 */

import { useEffect, useMemo, useState } from "react";
import { Button, Col, Flex, Form, Input, Row, Select, Typography } from "antd";

export type DraftField = { key: string; label: string; value: string; choices?: string[] };

export function DraftForm({
  fields,
  onSave,
}: {
  fields: DraftField[];
  onSave: (changed: Record<string, string>) => void;
}) {
  const committed = useMemo(
    () => Object.fromEntries(fields.map((field) => [field.key, field.value])),
    [fields],
  );
  // 依赖一个字符串而不是 committed 对象: 对象每次渲染都是新的, 会让下面那个 effect
  // 每渲染一次就把用户正在打的字冲掉一次。
  const signature = JSON.stringify(committed);
  const [draft, setDraft] = useState<Record<string, string>>(committed);
  // 服务端的值变了 (保存成功, 或别处改动后刷新) 才重置草稿, 不在每次渲染时覆盖输入。
  useEffect(() => {
    setDraft(JSON.parse(signature) as Record<string, string>);
  }, [signature]);
  const changed = Object.fromEntries(
    Object.entries(draft).filter(([key, value]) => committed[key] !== value),
  );
  const dirty = Object.keys(changed).length;
  const update = (key: string, value: string) => setDraft((state) => ({ ...state, [key]: value }));

  return (
    <Form layout="vertical" component="div" size="small">
      {/* 两列: 这些表单大多是十来个短字段, 一列排下去要滚很久才能看到保存按钮。 */}
      <Row gutter={12}>
        {fields.map((field) => (
          <Col xs={24} sm={12} key={field.key}>
            <Form.Item
              label={field.label}
              // 改过还没保存的那几行标出来: 一张表里哪几格待保存, 光看按钮上的数字看不出来。
              validateStatus={draft[field.key] !== committed[field.key] ? "warning" : undefined}
            >
              {field.choices ? (
                <Select
                  value={draft[field.key] ?? ""}
                  onChange={(value) => update(field.key, value)}
                  options={field.choices.map((choice) => ({ value: choice, label: choice }))}
                />
              ) : (
                <Input
                  value={draft[field.key] ?? ""}
                  onChange={(event) => update(field.key, event.target.value)}
                />
              )}
            </Form.Item>
          </Col>
        ))}
      </Row>
      <Flex gap="small" align="center" justify="flex-end" wrap>
        <Typography.Text type="secondary">{dirty ? `${dirty} 项待保存` : "没有改动"}</Typography.Text>
        <Button size="small" onClick={() => setDraft(committed)} disabled={!dirty}>
          撤销
        </Button>
        <Button size="small" type="primary" onClick={() => onSave(changed)} disabled={!dirty}>
          保存
        </Button>
      </Flex>
    </Form>
  );
}
