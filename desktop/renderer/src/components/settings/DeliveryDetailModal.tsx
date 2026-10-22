import React, { useRef } from "react";
import type { ChannelDelivery } from "../../../../shared/channels.js";
import { Button } from "../Button.js";
import { MarkdownContent } from "../MarkdownContent.js";
import { useDialogFocusLifecycle } from "../useDialogFocusLifecycle.js";
import { StatusBadge, type StatusTone } from "../StatusBadge.js";

interface DeliveryDetailModalProps {
  delivery: ChannelDelivery;
  stateLabel: string;
  stateTone: StatusTone;
  onClose: () => void;
  onCopy: () => void;
}

export function DeliveryDetailModal({
  delivery,
  stateLabel,
  stateTone,
  onClose,
  onCopy,
}: DeliveryDetailModalProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useDialogFocusLifecycle({ open: true, initialFocusRef: closeRef });

  return (
    <div
      className="modal-backdrop channel-delivery-modal-backdrop"
      onClick={onClose}
    >
      <div
        className="channel-delivery-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delivery-modal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="channel-delivery-modal-header">
          <div className="channel-delivery-modal-header-left">
            <h3 id="delivery-modal-title" className="channel-delivery-modal-title">
              回复详情
            </h3>
            <StatusBadge tone={stateTone} dot>
              {stateLabel}
            </StatusBadge>
            {delivery.createdAt && (
              <span className="channel-delivery-time">
                {new Date(delivery.createdAt).toLocaleString([], {
                  month: "numeric",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            )}
          </div>
          <div className="channel-delivery-modal-header-actions">
            <Button size="small" variant="secondary" onClick={onCopy}>
              复制内容
            </Button>
            <button
              ref={closeRef}
              type="button"
              className="channel-delivery-modal-close-btn"
              aria-label="关闭回复详情"
              onClick={onClose}
            >
              ×
            </button>
          </div>
        </div>

        <div className="channel-delivery-modal-body">
          <div className="channel-delivery-markdown-wrap">
            <MarkdownContent className="eidos-chat-prose" content={delivery.text} />
          </div>
          {delivery.reason && (
            <div className="channel-delivery-reason" style={{ marginTop: "1rem" }}>
              <span>{delivery.reason}</span>
            </div>
          )}
        </div>

        <div className="channel-delivery-modal-footer">
          <Button variant="secondary" size="small" onClick={onClose}>
            关闭
          </Button>
        </div>
      </div>
    </div>
  );
}
