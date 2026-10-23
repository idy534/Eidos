import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AlertBanner } from "./AlertBanner.js";
import { ConfirmDialog } from "./ConfirmDialog.js";
import { EmptyState } from "./EmptyState.js";
import { FieldError } from "./FieldError.js";
import { Kbd } from "./Kbd.js";
import { SearchInput } from "./SearchInput.js";
import { SettingSection } from "./SettingSection.js";
import { Spinner } from "./Spinner.js";
import { ToastContainer } from "./Toast.js";

describe("Global common components", () => {
  describe("SearchInput", () => {
    it("renders search input with value, placeholder and handles typing", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      render(
        <SearchInput
          value="hello"
          onChange={onChange}
          placeholder="搜索项目..."
          ariaLabel="搜索测试"
        />,
      );

      const input = screen.getByRole("searchbox", { name: "搜索测试" });
      expect(input).toBeInTheDocument();
      expect(input).toHaveValue("hello");

      await user.type(input, " world");
      expect(onChange).toHaveBeenCalled();
    });

    it("clears search value when clear button is clicked", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      const onClear = vi.fn();
      render(
        <SearchInput
          value="query"
          onChange={onChange}
          onClear={onClear}
          ariaLabel="搜索"
        />,
      );

      const clearBtn = screen.getByRole("button", { name: "清空搜索" });
      expect(clearBtn).toBeInTheDocument();

      await user.click(clearBtn);
      expect(onChange).toHaveBeenCalledWith("");
      expect(onClear).toHaveBeenCalled();
    });

    it("clears search on Escape key press", () => {
      const onChange = vi.fn();
      render(
        <SearchInput
          value="test query"
          onChange={onChange}
          ariaLabel="搜索"
        />,
      );

      const input = screen.getByRole("searchbox", { name: "搜索" });
      fireEvent.keyDown(input, { key: "Escape" });
      expect(onChange).toHaveBeenCalledWith("");
    });
  });

  describe("EmptyState", () => {
    it("renders title, description, icon and action", () => {
      render(
        <EmptyState
          icon={<span data-testid="empty-icon">📁</span>}
          title="没有项目"
          description="点击下方按钮创建第一个项目"
          action={<button type="button">新建项目</button>}
        />,
      );

      expect(screen.getByText("没有项目")).toBeInTheDocument();
      expect(screen.getByText("点击下方按钮创建第一个项目")).toBeInTheDocument();
      expect(screen.getByTestId("empty-icon")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "新建项目" })).toBeInTheDocument();
    });
  });

  describe("AlertBanner", () => {
    it("renders error tone with role alert", () => {
      render(
        <AlertBanner tone="error" title="保存失败">
          网络连接超时，请重试。
        </AlertBanner>,
      );

      const alert = screen.getByRole("alert");
      expect(alert).toHaveClass("alert-banner--error");
      expect(screen.getByText("保存失败")).toBeInTheDocument();
      expect(screen.getByText("网络连接超时，请重试。")).toBeInTheDocument();
    });

    it("renders info tone with role status and supports close", async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      render(
        <AlertBanner tone="info" onClose={onClose}>
          已更新到最新版本。
        </AlertBanner>,
      );

      const status = screen.getByRole("status");
      expect(status).toHaveClass("alert-banner--info");

      const closeBtn = screen.getByRole("button", { name: "关闭提示" });
      await user.click(closeBtn);
      expect(onClose).toHaveBeenCalled();
    });
  });

  describe("Spinner", () => {
    it("renders with default size and tone", () => {
      render(<Spinner ariaLabel="加载中..." />);
      const spinner = screen.getByRole("status", { name: "加载中..." });
      expect(spinner).toHaveClass("spinner--md");
      expect(spinner).toHaveClass("spinner--current");
    });

    it("renders custom size and tone", () => {
      render(<Spinner size="lg" tone="accent" ariaLabel="保存中" />);
      const spinner = screen.getByRole("status", { name: "保存中" });
      expect(spinner).toHaveClass("spinner--lg");
      expect(spinner).toHaveClass("spinner--accent");
    });
  });


  describe("Kbd", () => {
    it("renders keyboard shortcut keycap", () => {
      render(<Kbd>⌘K</Kbd>);
      const kbd = screen.getByText("⌘K");
      expect(kbd.tagName.toLowerCase()).toBe("kbd");
      expect(kbd).toHaveClass("kbd-key");
    });
  });

  describe("SettingSection", () => {
    it("renders header title, description, action and children", () => {
      render(
        <SettingSection
          title="通用设置"
          description="配置基础偏好项"
          headerAction={<button type="button">重置</button>}
        >
          <div data-testid="content">设置项内容</div>
        </SettingSection>,
      );

      expect(screen.getByText("通用设置")).toBeInTheDocument();
      expect(screen.getByText("配置基础偏好项")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "重置" })).toBeInTheDocument();
      expect(screen.getByTestId("content")).toBeInTheDocument();
    });
  });

  describe("ConfirmDialog re-export", () => {
    it("renders open confirmation dialog", () => {
      const onConfirm = vi.fn();
      const onCancel = vi.fn();
      render(
        <ConfirmDialog
          open={true}
          title="确认删除此项？"
          description="该操作不可撤销。"
          onConfirm={onConfirm}
          onCancel={onCancel}
        />,
      );

      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      expect(screen.getByText("确认删除此项？")).toBeInTheDocument();
      expect(screen.getByText("该操作不可撤销。")).toBeInTheDocument();
    });
  });

  describe("ToastContainer", () => {
    it("renders toast items at top center with messages, icons and correct ARIA roles", () => {
      const onDismiss = vi.fn();
      render(
        <ToastContainer
          toasts={[
            { id: "1", message: "操作成功完成", type: "success" },
            { id: "2", message: "配置已被变更", type: "warning" },
            { id: "3", message: "请求执行失败", type: "error" },
            { id: "4", message: "正在同步数据", type: "info" },
          ]}
          onDismiss={onDismiss}
        />,
      );

      expect(screen.getByText("操作成功完成")).toBeInTheDocument();
      expect(screen.getByText("配置已被变更")).toBeInTheDocument();
      expect(screen.getByText("请求执行失败")).toBeInTheDocument();
      expect(screen.getByText("正在同步数据")).toBeInTheDocument();

      const alerts = screen.getAllByRole("alert");
      expect(alerts).toHaveLength(2); // error + warning

      const statuses = screen.getAllByRole("status");
      expect(statuses).toHaveLength(2); // success + info
    });

    it("triggers onDismiss when close button is clicked", () => {
      const onDismiss = vi.fn();
      render(
        <ToastContainer
          toasts={[{ id: "t1", message: "提示测试", type: "info" }]}
          onDismiss={onDismiss}
        />,
      );

      const closeBtn = screen.getByRole("button", { name: "关闭提示" });
      fireEvent.click(closeBtn);
      expect(onDismiss).toHaveBeenCalledWith("t1");
    });

    it("automatically dismisses toast after configured duration", () => {
      vi.useFakeTimers();
      try {
        const onDismiss = vi.fn();
        render(
          <ToastContainer
            toasts={[{ id: "auto1", message: "自动消失", type: "success", duration: 2000 }]}
            onDismiss={onDismiss}
          />,
        );

        expect(onDismiss).not.toHaveBeenCalled();
        vi.advanceTimersByTime(2000);
        expect(onDismiss).toHaveBeenCalledWith("auto1");
      } finally {
        vi.useRealTimers();
      }
    });
  });

  describe("FieldError", () => {
    it("renders error message with alert role and subtle icon", () => {
      render(<FieldError error="用户名不能为空" />);
      const alert = screen.getByRole("alert");
      expect(alert).toBeInTheDocument();
      expect(alert).toHaveTextContent("用户名不能为空");
      expect(alert).toHaveClass("field-error");
      expect(alert.querySelector("svg")).toBeInTheDocument();
    });

    it("renders children syntax and custom tag/inline mode", () => {
      render(
        <FieldError as="span" inline icon={false}>
          必须填写有效数值
        </FieldError>,
      );
      const alert = screen.getByRole("alert");
      expect(alert.tagName.toLowerCase()).toBe("span");
      expect(alert).toHaveClass("field-error", "field-error--inline");
      expect(alert).toHaveTextContent("必须填写有效数值");
      expect(alert.querySelector("svg")).not.toBeInTheDocument();
    });

    it("returns null when error is empty or null", () => {
      const { container } = render(<FieldError error="" />);
      expect(container.firstChild).toBeNull();
    });
  });
});
