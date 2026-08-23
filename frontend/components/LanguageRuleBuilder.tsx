"use client";

import React from "react";

export type RuleNode = GroupNode | ConditionNode;

export interface GroupNode {
  type: "group";
  op: "AND" | "OR";
  rules: RuleNode[];
}

export interface ConditionNode {
  type: "condition";
  field: "audio" | "subtitle";
  languages: string[];
}

export const COMMON_LANGUAGES = [
  { code: "EN", name: "English" },
  { code: "IT", name: "Italian" },
  { code: "JA", name: "Japanese" },
  { code: "ES", name: "Spanish" },
  { code: "FR", name: "French" },
  { code: "DE", name: "German" },
  { code: "ZH", name: "Chinese" },
  { code: "KO", name: "Korean" },
  { code: "PT", name: "Portuguese" },
];

interface LanguageRuleBuilderProps {
  rule: GroupNode;
  onChange: (updatedRule: GroupNode) => void;
}

export default function LanguageRuleBuilder({ rule, onChange }: LanguageRuleBuilderProps) {
  const handleOpChange = (newOp: "AND" | "OR") => {
    onChange({ ...rule, op: newOp });
  };

  const handleAddCondition = () => {
    const newCondition: ConditionNode = {
      type: "condition",
      field: "audio",
      languages: ["EN"],
    };
    onChange({ ...rule, rules: [...rule.rules, newCondition] });
  };

  const handleAddGroup = () => {
    const newGroup: GroupNode = {
      type: "group",
      op: "AND",
      rules: [],
    };
    onChange({ ...rule, rules: [...rule.rules, newGroup] });
  };

  const handleRuleChange = (index: number, updatedChild: RuleNode) => {
    const updatedRules = [...rule.rules];
    updatedRules[index] = updatedChild;
    onChange({ ...rule, rules: updatedRules });
  };

  const handleRemoveRule = (index: number) => {
    const updatedRules = rule.rules.filter((_, i) => i !== index);
    onChange({ ...rule, rules: updatedRules });
  };

  return (
    <div className="rounded-xl border border-gray-300 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-800">
      {/* Group Header */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 pb-3 dark:border-gray-700">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
            Match
          </span>
          <div className="inline-flex rounded-lg border border-gray-200 bg-gray-100 p-0.5 dark:border-gray-700 dark:bg-gray-900">
            <button
              type="button"
              onClick={() => handleOpChange("AND")}
              className={`rounded-md px-3 py-1 text-xs font-bold transition-colors ${
                rule.op === "AND"
                  ? "bg-blue-600 text-white shadow"
                  : "text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white"
              }`}
            >
              ALL (AND)
            </button>
            <button
              type="button"
              onClick={() => handleOpChange("OR")}
              className={`rounded-md px-3 py-1 text-xs font-bold transition-colors ${
                rule.op === "OR"
                  ? "bg-purple-600 text-white shadow"
                  : "text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white"
              }`}
            >
              ANY (OR)
            </button>
          </div>
          <span className="text-xs text-gray-500 dark:text-gray-400">
            of the following conditions:
          </span>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleAddCondition}
            className="rounded-lg border border-blue-600 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100 dark:border-blue-500 dark:bg-blue-950/40 dark:text-blue-300 dark:hover:bg-blue-900/60"
          >
            + Add Condition
          </button>
          <button
            type="button"
            onClick={handleAddGroup}
            className="rounded-lg border border-purple-600 bg-purple-50 px-3 py-1.5 text-xs font-medium text-purple-700 hover:bg-purple-100 dark:border-purple-500 dark:bg-purple-950/40 dark:text-purple-300 dark:hover:bg-purple-900/60"
          >
            + Add Group
          </button>
        </div>
      </div>

      {/* Rules list */}
      {rule.rules.length === 0 ? (
        <div className="py-4 text-center text-xs text-gray-400 dark:text-gray-500">
          No conditions in this group. Click &quot;+ Add Condition&quot; or &quot;+ Add Group&quot; to start building rules.
        </div>
      ) : (
        <div className="space-y-3">
          {rule.rules.map((child, index) => (
            <div key={index} className="flex items-start gap-2">
              <div className="flex-1">
                {child.type === "group" ? (
                  <LanguageRuleBuilder
                    rule={child}
                    onChange={(updated) => handleRuleChange(index, updated)}
                  />
                ) : (
                  <ConditionRow
                    condition={child}
                    onChange={(updated) => handleRuleChange(index, updated)}
                  />
                )}
              </div>
              <button
                type="button"
                onClick={() => handleRemoveRule(index)}
                className="mt-2 rounded-lg p-1 text-gray-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40 dark:hover:text-red-400"
                title="Remove rule"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

interface ConditionRowProps {
  condition: ConditionNode;
  onChange: (updated: ConditionNode) => void;
}

function ConditionRow({ condition, onChange }: ConditionRowProps) {
  const [inputVal, setInputVal] = React.useState(condition.languages.join(", "));

  React.useEffect(() => {
    setInputVal(condition.languages.join(", "));
  }, [condition.languages]);

  const handleFieldChange = (newField: "audio" | "subtitle") => {
    onChange({ ...condition, field: newField });
  };

  const handleLangsBlur = () => {
    const parsed = inputVal
      .split(",")
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    onChange({ ...condition, languages: parsed });
  };

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-700 dark:bg-gray-900/60">
      <select
        value={condition.field}
        onChange={(e) => handleFieldChange(e.target.value as "audio" | "subtitle")}
        className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-800 shadow-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200"
      >
        <option value="audio">Audio Track</option>
        <option value="subtitle">Subtitle Track</option>
      </select>

      <span className="text-xs text-gray-500 dark:text-gray-400">contains any of:</span>

      <input
        type="text"
        value={inputVal}
        onChange={(e) => setInputVal(e.target.value)}
        onBlur={handleLangsBlur}
        placeholder="EN, IT, JA"
        className="flex-1 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-mono shadow-sm placeholder-gray-400 focus:border-blue-500 focus:outline-none dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
      />
    </div>
  );
}
