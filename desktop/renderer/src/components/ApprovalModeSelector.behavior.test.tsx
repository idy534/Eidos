import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApprovalModeSelector } from "./ApprovalModeSelector.js";

describe("ApprovalModeSelector", () => {
  it("renders the trigger button with the current mode label", () => {
    render(<ApprovalModeSelector mode="manual" onChange={vi.fn()} />);

    const trigger = screen.getByRole("button", { name: "审批模式：请求审批" });
    expect(trigger).toHaveTextContent("请求审批");
    expect(trigger).not.toHaveClass("approval-mode-selector__trigger--danger");
  });

  it("applies danger styling when mode is full_access", () => {
    render(<ApprovalModeSelector mode="full_access" onChange={vi.fn()} />);

    const trigger = screen.getByRole("button", { name: "审批模式：完全访问" });
    expect(trigger).toHaveTextContent("完全访问");
    expect(trigger).toHaveClass("approval-mode-selector__trigger--danger");
  });

  it("opens the panel on click and renders all mode options with descriptions and badges", () => {
    render(<ApprovalModeSelector mode="manual" onChange={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "审批模式：请求审批" }));

    const dialog = screen.getByRole("dialog", { name: "选择审批模式" });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText("请求审批")).toBeInTheDocument();
    expect(within(dialog).getByText("需要审批时由你批准或拒绝。")).toBeInTheDocument();

    expect(within(dialog).getByText("替我审批")).toBeInTheDocument();
    expect(within(dialog).getByText("推荐")).toBeInTheDocument();
    expect(within(dialog).getByText("模型处理原本需要审批的操作")).toBeInTheDocument();

    expect(within(dialog).getByText("完全访问")).toBeInTheDocument();
    expect(within(dialog).getByText("风险")).toBeInTheDocument();
    expect(within(dialog).getByText("完全访问互联网和电脑上的所有文件。")).toBeInTheDocument();
  });

  it("selects auto_review immediately without confirmation", () => {
    const onChange = vi.fn();
    render(<ApprovalModeSelector mode="manual" onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "审批模式：请求审批" }));
    const autoReviewOption = screen.getByRole("radio", { name: /替我审批/ });
    fireEvent.click(autoReviewOption);

    expect(onChange).toHaveBeenCalledWith("auto_review");
    expect(screen.queryByRole("dialog", { name: "选择审批模式" })).not.toBeInTheDocument();
  });

  it("opens ConfirmDialog when selecting full_access and handles cancel / confirm", () => {
    const onChange = vi.fn();
    const { rerender } = render(<ApprovalModeSelector mode="manual" onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "审批模式：请求审批" }));
    const fullAccessOption = screen.getByRole("radio", { name: /完全访问/ });
    fireEvent.click(fullAccessOption);

    // ConfirmDialog should be visible, onChange should not be called yet
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByText("开启完全访问？")).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();

    // Clicking cancel closes dialog without calling onChange
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();

    // Open again and confirm
    fireEvent.click(screen.getByRole("button", { name: "审批模式：请求审批" }));
    fireEvent.click(screen.getByRole("radio", { name: /完全访问/ }));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "开启完全访问" }));
    expect(onChange).toHaveBeenCalledWith("full_access");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    // When already in full_access, clicking full_access does not re-trigger ConfirmDialog
    rerender(<ApprovalModeSelector mode="full_access" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "审批模式：完全访问" }));
    fireEvent.click(screen.getByRole("radio", { name: /完全访问/ }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("closes the panel on Escape key", () => {
    render(<ApprovalModeSelector mode="manual" onChange={vi.fn()} />);

    const trigger = screen.getByRole("button", { name: "审批模式：请求审批" });
    fireEvent.click(trigger);
    expect(screen.getByRole("dialog", { name: "选择审批模式" })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "选择审批模式" })).not.toBeInTheDocument();
  });
});
