/** 模型的提问卡片。推荐项不自动选中；提交失败保留用户的选择和草稿。 */

import { useRef, useState } from "react";
import { Alert, Button, Card, Checkbox, Flex, Form, Input, Radio, Space, Tag, Typography } from "antd";
import type { Prompt, PromptChoice } from "@/shared/lib/approval";

export type ResolvePrompt = (
  id: string,
  choice: string,
  text?: string,
  selectedValues?: string[],
  skipped?: boolean,
) => Promise<void>;

export function QuestionCard({ prompt, onResolve }: { prompt: Prompt; onResolve: ResolvePrompt }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const multiple = prompt.selection_mode === "multiple";
  const answerable = selected.length > 0 || Boolean(text.trim());

  async function answer(skipped: boolean) {
    if (inFlight.current) return;
    inFlight.current = true;
    setSending(true);
    setError("");
    try {
      await onResolve(prompt.prompt_id, "", skipped ? "" : text.trim(), skipped ? [] : selected, skipped);
    } catch (reason) {
      setError((reason as Error).message || "提交失败，请重试");
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  }

  const label = (option: PromptChoice) => (
    <Flex vertical gap={2}>
      <Space size={4}>
        {option.label}
        {prompt.recommended_option_id === option.value && <Tag color="green">推荐</Tag>}
      </Space>
      {option.detail && <Typography.Text type="secondary">{option.detail}</Typography.Text>}
    </Flex>
  );

  return (
    <Card
      className="attention-card question-card"
      size="small"
      title="Forge · 需要你的意见"
      extra={<Tag color={sending ? "processing" : "gold"}>{sending ? "正在提交…" : "等待回答"}</Tag>}
    >
      <Form
        layout="vertical"
        onFinish={() => {
          if (answerable) void answer(false);
        }}
      >
        <Typography.Title level={5} id={`question-${prompt.prompt_id}`}>
          {prompt.title}
        </Typography.Title>
        {prompt.body && <Typography.Paragraph>{prompt.body}</Typography.Paragraph>}
        {prompt.choices.length > 0 && (
          <Form.Item>
            {multiple ? (
              <Checkbox.Group
                aria-labelledby={`question-${prompt.prompt_id}`}
                value={selected}
                onChange={setSelected}
              >
                <Space direction="vertical">
                  {prompt.choices.map((option) => (
                    <Checkbox value={option.value} disabled={sending} key={option.value}>
                      {label(option)}
                    </Checkbox>
                  ))}
                </Space>
              </Checkbox.Group>
            ) : (
              <Radio.Group
                aria-labelledby={`question-${prompt.prompt_id}`}
                value={selected[0]}
                onChange={(event) => setSelected([event.target.value])}
              >
                <Space direction="vertical">
                  {prompt.choices.map((option) => (
                    <Radio value={option.value} disabled={sending} key={option.value}>
                      {label(option)}
                    </Radio>
                  ))}
                </Space>
              </Radio.Group>
            )}
          </Form.Item>
        )}
        {prompt.free_text && (
          <Form.Item label="补充说明（可选）">
            <Input.TextArea
              rows={2}
              value={text}
              disabled={sending}
              onChange={(event) => setText(event.target.value)}
              placeholder="也可以不选选项，直接写下你的想法…"
            />
          </Form.Item>
        )}
        {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 8 }} />}
        <Flex gap="small" wrap justify="flex-end" align="center">
          <Typography.Text type="secondary" aria-live="polite">
            {prompt.choices.length
              ? `${multiple ? "可多选" : "单选"} · 已选 ${selected.length} 项`
              : "可以直接回答"}
          </Typography.Text>
          {prompt.allow_skip && (
            <Button disabled={sending} onClick={() => void answer(true)}>
              跳过本题
            </Button>
          )}
          <Button type="primary" htmlType="submit" loading={sending} disabled={!answerable}>
            提交回答
          </Button>
        </Flex>
      </Form>
    </Card>
  );
}
