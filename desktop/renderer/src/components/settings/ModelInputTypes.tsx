interface Props {
  supportsImages: boolean;
  supportsToolCall: boolean;
  disabled: boolean;
  onImagesChange: (value: boolean) => void;
  onToolCallChange: (value: boolean) => void;
}

export function ModelInputTypes({ supportsImages, supportsToolCall, disabled, onImagesChange, onToolCallChange }: Props) {
  return <div className="custom-model-capabilities"><span>输入类型</span><div>
    <label className="custom-model-check"><input type="checkbox" checked disabled />文本</label>
    <label className="custom-model-check"><input type="checkbox" checked={supportsImages} disabled={disabled} onChange={event => onImagesChange(event.target.checked)} />图像</label>
    <label className="custom-model-check"><input type="checkbox" checked={supportsToolCall} disabled={disabled} onChange={event => onToolCallChange(event.target.checked)} />支持工具调用</label>
  </div></div>;
}
