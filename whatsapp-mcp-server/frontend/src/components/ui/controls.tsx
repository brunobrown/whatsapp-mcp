import { Check, ChevronDown } from "lucide-react";
import { Checkbox, Select as SelectPrimitive, Switch as SwitchPrimitive } from "radix-ui";
import { useId } from "react";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Name each control with a <label htmlFor>, aria-label or a legend. Wire problems with `aria-invalid` and
 * `aria-describedby`, which land on the focusable element.
 */

export function Switch({ className, ...props }: ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      className={cn(
        // The ::before widens the hit area to the 44 px touch target without growing the track.
        "relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full bg-muted p-0.5",
        "before:absolute before:-inset-x-1 before:-inset-y-2.5 before:content-['']",
        "transition-colors duration-150 data-[state=checked]:bg-accent-solid",
        "disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:ring-2 aria-invalid:ring-danger-ink",
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="block size-5 rounded-full bg-on-accent shadow-sm transition-transform duration-150 data-[state=checked]:translate-x-5" />
    </SwitchPrimitive.Root>
  );
}

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectProps
  extends Pick<ComponentProps<"button">, "id" | "aria-label" | "aria-labelledby" | "aria-describedby" | "aria-invalid" | "className"> {
  value: string;
  onValueChange: (value: string) => void;
  options: readonly SelectOption[];
  placeholder?: string;
  disabled?: boolean;
  name?: string;
}

export function Select({ value, onValueChange, options, placeholder, disabled, name, className, ...aria }: SelectProps) {
  return (
    <SelectPrimitive.Root value={value} onValueChange={onValueChange} disabled={disabled} name={name}>
      <SelectPrimitive.Trigger
        className={cn(
          "flex min-h-(--min-target) w-full items-center justify-between gap-2 rounded-control border border-line bg-panel px-3 text-left text-ink",
          "data-[placeholder]:text-muted disabled:cursor-not-allowed disabled:opacity-60 aria-invalid:border-danger-ink",
          className,
        )}
        {...aria}
      >
        <SelectPrimitive.Value placeholder={placeholder} />
        <SelectPrimitive.Icon asChild>
          <ChevronDown aria-hidden="true" className="size-4 shrink-0 text-muted" />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          position="popper"
          sideOffset={4}
          className="z-50 max-h-72 w-(--radix-select-trigger-width) min-w-40 overflow-hidden rounded-control border border-line bg-panel text-ink shadow-lg"
        >
          <SelectPrimitive.Viewport className="p-1">
            {options.map((option) => (
              <SelectPrimitive.Item
                key={option.value}
                value={option.value}
                className="relative flex min-h-(--min-target) cursor-pointer items-center rounded-sm py-2 pr-3 pl-9 outline-none select-none data-[highlighted]:bg-hover data-[state=checked]:font-medium"
              >
                <SelectPrimitive.ItemIndicator className="absolute left-2.5 inline-flex">
                  <Check aria-hidden="true" className="size-4 text-accent-ink" />
                </SelectPrimitive.ItemIndicator>
                <SelectPrimitive.ItemText>{option.label}</SelectPrimitive.ItemText>
              </SelectPrimitive.Item>
            ))}
          </SelectPrimitive.Viewport>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}

export interface CheckboxGroupProps {
  legend: ReactNode;
  options: readonly SelectOption[];
  value: readonly string[];
  onValueChange: (value: string[]) => void;
  invalid?: boolean;
  disabled?: boolean;
  "aria-describedby"?: string;
  className?: string;
}

export function CheckboxGroup({
  legend,
  options,
  value,
  onValueChange,
  invalid = false,
  disabled = false,
  className,
  "aria-describedby": describedBy,
}: CheckboxGroupProps) {
  const base = useId();
  const toggle = (optionValue: string, checked: boolean): void => {
    const chosen = new Set(value);
    if (checked) chosen.add(optionValue);
    else chosen.delete(optionValue);
    onValueChange(options.map((option) => option.value).filter((v) => chosen.has(v)));
  };
  return (
    <fieldset aria-describedby={describedBy} disabled={disabled} className={cn("m-0 min-w-0 border-0 p-0", className)}>
      <legend className="mb-1 p-0 font-medium text-ink">{legend}</legend>
      <div className="flex flex-col">
        {options.map((option, index) => {
          const id = `${base}-${String(index)}`;
          return (
            <div key={option.value} className="flex min-h-(--min-target) items-center gap-3">
              <Checkbox.Root
                id={id}
                checked={value.includes(option.value)}
                onCheckedChange={(checked) => {
                  toggle(option.value, checked === true);
                }}
                aria-invalid={invalid || undefined}
                className="relative flex size-5 shrink-0 cursor-pointer items-center justify-center rounded-sm border border-muted bg-panel before:absolute before:-inset-3 before:content-[''] data-[state=checked]:border-accent-solid data-[state=checked]:bg-accent-solid disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-danger-ink"
              >
                <Checkbox.Indicator>
                  <Check aria-hidden="true" className="size-3.5 text-on-accent" strokeWidth={3} />
                </Checkbox.Indicator>
              </Checkbox.Root>
              <label htmlFor={id} className="flex-1 cursor-pointer py-2">
                {option.label}
              </label>
            </div>
          );
        })}
      </div>
    </fieldset>
  );
}
