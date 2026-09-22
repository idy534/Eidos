import { useCallback, useEffect, useRef, useState } from "react";
import type {
  InputAnswer,
  InputQuestion,
  UserInputRequest,
} from "../../../shared/planning.generated.js";
import { userFacingError } from "../session-state.js";
import { Button } from "./Button.js";
import "./planning.css";

export interface ClarificationComposerProps {
  request: UserInputRequest;
  ready: boolean;
  onSaved: () => void;
}

export function ClarificationComposer({
  request,
  ready,
  onSaved,
}: ClarificationComposerProps) {
  const questions = request.questions;
  const [currentIndex, setCurrentIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, InputAnswer>>({});
  const [customSelected, setCustomSelected] = useState<Record<string, boolean>>({});
  const [skippedQuestions, setSkippedQuestions] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const lock = useRef(false);

  // Initialize recommended options if available
  useEffect(() => {
    setAnswers((prev) => {
      const initial: Record<string, InputAnswer> = { ...prev };
      let changed = false;
      for (const q of questions) {
        if (!initial[q.id] && q.recommendedOptionId) {
          initial[q.id] = {
            questionId: q.id,
            optionIds: [q.recommendedOptionId],
            text: "",
          };
          changed = true;
        }
      }
      return changed ? initial : prev;
    });
  }, [questions]);

  // Clamp current index if questions change
  useEffect(() => {
    if (currentIndex >= questions.length) {
      setCurrentIndex(Math.max(0, questions.length - 1));
    }
  }, [currentIndex, questions.length]);

  const updateAnswer = useCallback(
    (questionId: string, patch: Partial<InputAnswer>) => {
      setAnswers((previous) => ({
        ...previous,
        [questionId]: {
          questionId,
          ...previous[questionId],
          ...patch,
        },
      }));
      setError("");
    },
    [],
  );

  const isQuestionAnswered = useCallback(
    (question: InputQuestion) => {
      if (skippedQuestions[question.id]) {
        return true;
      }
      const a = answers[question.id];
      const isCustom = customSelected[question.id] ?? false;
      const text = a?.text?.trim() ?? "";
      const optionIds = a?.optionIds ?? [];

      if (question.type === "text") {
        return text.length > 0;
      }

      if (isCustom) {
        // When custom is selected, text is strictly required
        return text.length > 0;
      }

      return optionIds.length > 0;
    },
    [answers, customSelected, skippedQuestions],
  );

  const allAnswered = questions.every(isQuestionAnswered);
  const answeredCount = questions.filter(isQuestionAnswered).length;
  const currentQuestion = questions[currentIndex] ?? questions[0];
  const isCurrentSkipped = Boolean(currentQuestion && skippedQuestions[currentQuestion.id]);

  const submit = async (skipEntirely = false) => {
    if (lock.current) return;
    if (typeof window.eidosRuntime?.answerUserInput !== "function") {
      setError("当前环境不支持提交回答");
      return;
    }

    if (!skipEntirely && !allAnswered) {
      const firstUnanswered = questions.findIndex((q) => !isQuestionAnswered(q));
      if (firstUnanswered !== -1) {
        setCurrentIndex(firstUnanswered);
        const q = questions[firstUnanswered];
        if (q && customSelected[q.id]) {
          setError(`请在问题 ${firstUnanswered + 1} 的自定义选项中输入内容`);
        } else {
          setError(`请先完成问题 ${firstUnanswered + 1} 后再提交`);
        }
        return;
      }
    }

    lock.current = true;
    setBusy(true);
    setError("");

    try {
      const allSkipped = skipEntirely || questions.every((q) => skippedQuestions[q.id]);
      await window.eidosRuntime.answerUserInput({
        requestId: request.id,
        response: {
          status: allSkipped ? "skipped" : "answered",
          answers: allSkipped
            ? []
            : questions.map((q) => {
                if (skippedQuestions[q.id]) {
                  return {
                    questionId: q.id,
                    optionIds: [],
                    text: "跳过",
                  };
                }
                const a = answers[q.id];
                const isCustom = customSelected[q.id] ?? false;
                return {
                  questionId: q.id,
                  optionIds: a?.optionIds ?? [],
                  text: q.type === "text" || isCustom ? (a?.text?.trim() ?? "") : "",
                };
              }),
        },
      });
      onSaved();
    } catch (cause) {
      setError(userFacingError(cause));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };

  const handleSkipClick = () => {
    if (!currentQuestion) return;

    if (questions.length === 1) {
      void submit(true);
      return;
    }

    if (isCurrentSkipped) {
      // Toggle unskip
      setSkippedQuestions((prev) => ({ ...prev, [currentQuestion.id]: false }));
      updateAnswer(currentQuestion.id, { text: "" });
    } else {
      // Skip current question
      setSkippedQuestions((prev) => ({ ...prev, [currentQuestion.id]: true }));
      setCustomSelected((prev) => ({ ...prev, [currentQuestion.id]: false }));
      updateAnswer(currentQuestion.id, { optionIds: [], text: "跳过" });

      if (currentIndex < questions.length - 1) {
        setCurrentIndex((idx) => idx + 1);
      }
    }
  };

  // Keyboard shortcut: Cmd/Ctrl + Enter to submit when ready
  const handleKeyDown = (event: React.KeyboardEvent) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      if (allAnswered && ready && !busy) {
        void submit(false);
      }
    }
  };

  if (!currentQuestion) return null;

  const currentAnswer = answers[currentQuestion.id];
  const selectedOptionIds = currentAnswer?.optionIds ?? [];
  const customText = currentAnswer?.text ?? "";
  const isCustom = customSelected[currentQuestion.id] ?? false;

  const handleOptionToggle = (optionId: string) => {
    setSkippedQuestions((prev) => ({ ...prev, [currentQuestion.id]: false }));
    if (currentQuestion.type === "multi_select") {
      const next = selectedOptionIds.includes(optionId)
        ? selectedOptionIds.filter((id) => id !== optionId)
        : [...selectedOptionIds, optionId];
      updateAnswer(currentQuestion.id, { optionIds: next });
    } else {
      // single_select: select this option, deselect custom
      setCustomSelected((prev) => ({ ...prev, [currentQuestion.id]: false }));
      updateAnswer(currentQuestion.id, { optionIds: [optionId], text: "" });
    }
  };

  const handleCustomToggle = () => {
    setSkippedQuestions((prev) => ({ ...prev, [currentQuestion.id]: false }));
    if (currentQuestion.type === "multi_select") {
      setCustomSelected((prev) => {
        const next = !prev[currentQuestion.id];
        if (!next) {
          updateAnswer(currentQuestion.id, { text: "" });
        }
        return { ...prev, [currentQuestion.id]: next };
      });
    } else {
      // single_select: select custom, clear predefined options
      setCustomSelected((prev) => ({ ...prev, [currentQuestion.id]: true }));
      updateAnswer(currentQuestion.id, { optionIds: [] });
    }
  };

  return (
    <div
      className="clarification-composer"
      role="region"
      aria-label="澄清问题"
      onKeyDown={handleKeyDown}
    >
      {/* Header with Question Title & Question Type/Counter */}
      <div className="clarification-header">
        <div className="clarification-title-group">
          <ClarificationSparkIcon />
          <h3 className="clarification-question-title" title={currentQuestion.question}>
            {currentQuestion.question}
          </h3>
        </div>

        <div className="clarification-header-meta">
          {isCurrentSkipped && (
            <span className="clarification-skipped-tag">已跳过</span>
          )}
          <span className="clarification-type-badge">
            {currentQuestion.type === "multi_select"
              ? "多选"
              : currentQuestion.type === "text"
                ? "简答"
                : "单选"}
            {questions.length > 1 ? ` · 第 ${currentIndex + 1} / ${questions.length} 题` : ""}
          </span>
        </div>
      </div>

      {/* Question Body */}
      <div className="clarification-body">
        {/* Options list for choice questions */}
        {currentQuestion.type !== "text" && currentQuestion.options && (
          <div
            className="clarification-options"
            role={currentQuestion.type === "multi_select" ? "group" : "radiogroup"}
            aria-label={currentQuestion.question}
          >
            {currentQuestion.options.map((option) => {
              const isSelected = selectedOptionIds.includes(option.id);
              const isRecommended = currentQuestion.recommendedOptionId === option.id;

              return (
                <div
                  key={option.id}
                  className={[
                    "clarification-option",
                    isSelected ? "clarification-option--selected" : "",
                    busy ? "clarification-option--disabled" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  onClick={() => !busy && handleOptionToggle(option.id)}
                  role={currentQuestion.type === "multi_select" ? "checkbox" : "radio"}
                  aria-checked={isSelected}
                  tabIndex={busy ? -1 : 0}
                  onKeyDown={(e) => {
                    if (e.key === " " || e.key === "Enter") {
                      e.preventDefault();
                      if (!busy) handleOptionToggle(option.id);
                    }
                  }}
                >
                  <div className="clarification-option-indicator">
                    {currentQuestion.type === "multi_select" ? (
                      <span className={`clarification-checkbox${isSelected ? " checked" : ""}`}>
                        {isSelected && <CheckIcon />}
                      </span>
                    ) : (
                      <span className={`clarification-radio${isSelected ? " checked" : ""}`}>
                        {isSelected && <span className="clarification-radio-inner" />}
                      </span>
                    )}
                  </div>

                  <div className="clarification-option-content">
                    <div className="clarification-option-header">
                      <span className="clarification-option-label">{option.label}</span>
                      {isRecommended && (
                        <span className="clarification-recommended-tag">推荐</span>
                      )}
                    </div>
                    {option.description && (
                      <p className="clarification-option-description">
                        {option.description}
                      </p>
                    )}
                  </div>
                </div>
              );
            })}

            {/* Custom option as the last item */}
            <div
              className={[
                "clarification-option",
                isCustom ? "clarification-option--selected" : "",
                busy ? "clarification-option--disabled" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              onClick={() => !busy && handleCustomToggle()}
              role={currentQuestion.type === "multi_select" ? "checkbox" : "radio"}
              aria-checked={isCustom}
              tabIndex={busy ? -1 : 0}
              onKeyDown={(e) => {
                if (e.key === " " || e.key === "Enter") {
                  e.preventDefault();
                  if (!busy) handleCustomToggle();
                }
              }}
            >
              <div className="clarification-option-indicator">
                {currentQuestion.type === "multi_select" ? (
                  <span className={`clarification-checkbox${isCustom ? " checked" : ""}`}>
                    {isCustom && <CheckIcon />}
                  </span>
                ) : (
                  <span className={`clarification-radio${isCustom ? " checked" : ""}`}>
                    {isCustom && <span className="clarification-radio-inner" />}
                  </span>
                )}
              </div>

              <div className="clarification-option-content">
                <div className="clarification-option-header">
                  <span className="clarification-option-label">自定义</span>
                </div>
              </div>
            </div>

            {/* Input field when custom is selected */}
            {isCustom && (
              <div className="clarification-custom-input-wrap">
                <textarea
                  className="clarification-textarea clarification-textarea--custom"
                  rows={2}
                  maxLength={8000}
                  placeholder="请输入自定义内容（必填）…"
                  value={customText}
                  disabled={busy || !ready}
                  autoFocus
                  onChange={(e) => {
                    setSkippedQuestions((prev) => ({ ...prev, [currentQuestion.id]: false }));
                    updateAnswer(currentQuestion.id, { text: e.target.value });
                  }}
                />
              </div>
            )}
          </div>
        )}

        {/* Text Input: Only for 'text' question type */}
        {currentQuestion.type === "text" && (
          <div className="clarification-text-section">
            <label className="clarification-text-label">
              <span>你的回答</span>
              <textarea
                className="clarification-textarea"
                rows={2}
                maxLength={8000}
                placeholder="请输入你的回答…"
                value={customText}
                disabled={busy || !ready}
                onChange={(e) => {
                  setSkippedQuestions((prev) => ({ ...prev, [currentQuestion.id]: false }));
                  updateAnswer(currentQuestion.id, { text: e.target.value });
                }}
              />
            </label>
          </div>
        )}

        {error && (
          <p className="clarification-error" role="alert">
            {error}
          </p>
        )}
      </div>

      {/* Footer Navigation and Actions */}
      <div className="clarification-footer">
        <div className="clarification-footer-left">
          <Button
            type="button"
            variant="ghost"
            size="small"
            disabled={!ready || busy}
            onClick={handleSkipClick}
          >
            {questions.length === 1
              ? "跳过"
              : isCurrentSkipped
                ? "取消跳过"
                : "跳过此题"}
          </Button>
        </div>

        <div className="clarification-footer-right">
          {questions.length > 1 && (
            <>
              <Button
                type="button"
                variant="secondary"
                size="small"
                disabled={currentIndex === 0 || busy}
                onClick={() => setCurrentIndex((idx) => Math.max(0, idx - 1))}
              >
                上一个
              </Button>
              {currentIndex < questions.length - 1 ? (
                <Button
                  type="button"
                  variant="secondary"
                  size="small"
                  disabled={busy}
                  onClick={() => setCurrentIndex((idx) => Math.min(questions.length - 1, idx + 1))}
                >
                  下一个
                </Button>
              ) : null}
            </>
          )}

          <Button
            type="button"
            variant="primary"
            size="small"
            disabled={!ready || busy || !allAnswered}
            loading={busy}
            onClick={() => void submit(false)}
            title={
              !allAnswered
                ? `还剩 ${questions.length - answeredCount} 个问题未完成`
                : "提交回答 (Cmd+Enter)"
            }
          >
            提交回答
            {questions.length > 1 && ` (${answeredCount}/${questions.length})`}
          </Button>
        </div>
      </div>
    </div>
  );
}

function ClarificationSparkIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path
        d="M8 1.5C8.3 4.5 9.5 5.7 12.5 6C9.5 6.3 8.3 7.5 8 10.5C7.7 7.5 6.5 6.3 3.5 6C6.5 5.7 7.7 4.5 8 1.5Z"
        fill="currentColor"
      />
      <path
        d="M12.5 10C12.7 11.5 13.3 12.1 14.8 12.3C13.3 12.4 12.7 13 12.5 14.5C12.3 13 11.7 12.4 10.2 12.3C11.7 12.1 12.3 11.5 12.5 10Z"
        fill="currentColor"
        opacity="0.8"
      />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 12 12" width="10" height="10" fill="none" aria-hidden="true">
      <path
        d="M2.5 6.5L4.8 8.8L9.5 3.5"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
