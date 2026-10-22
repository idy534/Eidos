import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AlertBanner } from "./AlertBanner.js";
import { Badge } from "./Badge.js";
import { ConfirmDialog } from "./ConfirmDialog.js";
import { EmptyState } from "./EmptyState.js";
import { Kbd } from "./Kbd.js";
import { SearchInput } from "./SearchInput.js";
import { SettingSection } from "./SettingSection.js";
import { Spinner } from "./Spinner.js";

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

  describe("Badge", () => {
    it("renders variants and tones correctly", () => {
      const { rerender } = render(
        <Badge variant="subtle" tone="accent">
          推荐
        </Badge>,
      );
      expect(screen.getByText("推荐")).toHaveClass("badge--subtle", "badge--accent");

      rerender(
        <Badge variant="count" tone="danger">
          5
        </Badge>,
      );
      expect(screen.getByText("5")).toHaveClass("badge--count", "badge--danger");
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
});
