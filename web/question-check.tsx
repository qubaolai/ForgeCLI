import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { QuestionCard } from "./src/features/humanInteraction/QuestionCard";
import "./src/styles.css";
function Check() {
  const [mode, setMode] = useState<"single" | "multiple">("multiple");
  const [output, setOutput] = useState("");
  const [fail, setFail] = useState(false);
  return (
    <main style={{ maxWidth: 700, margin: "24px auto", padding: 16 }}>
      <button
        onClick={() => {
          setMode(mode === "single" ? "multiple" : "single");
          setOutput("");
        }}
      >
        切换问题类型
      </button>
      <label>
        <input type="checkbox" checked={fail} onChange={(e) => setFail(e.target.checked)} />
        模拟提交失败
      </label>
      <QuestionCard
        key={mode}
        prompt={{
          prompt_id: mode,
          kind: "question",
          title: "这次需要实现哪些阶段？",
          body: "选择需要实现的阶段",
          choices: [
            { value: "one", label: "实现阶段 1", detail: "完善问题卡片" },
            { value: "two", label: "实现阶段 2", detail: "接入结构化回答" },
            { value: "three", label: "实现阶段 3", detail: "修复即时展示" },
          ],
          free_text: true,
          detail: {},
          selection_mode: mode,
          recommended_option_id: "one",
          allow_skip: true,
        }}
        onResolve={async (...args) => {
          if (fail) throw new Error("提交失败，请重试");
          setOutput(JSON.stringify(args));
        }}
      />
      <output>{output}</output>
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<Check />);
