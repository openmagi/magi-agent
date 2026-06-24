// Input + Textarea route through the canonical design-system fields (shared
// look + a11y with cp). The custom combobox stays in ./select.tsx.
import { Input as DsInput, type InputProps } from "./_ds/Input";
import { Textarea as DsTextarea, type TextareaProps } from "./_ds/Input";

export function Input(props: InputProps) {
  return <DsInput {...props} />;
}

export function Textarea(props: TextareaProps) {
  return <DsTextarea {...props} />;
}
