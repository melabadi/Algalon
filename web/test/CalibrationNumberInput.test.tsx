import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLayoutEffect, useState } from 'react';
import { expect, test, vi } from 'vitest';
import { CalibrationNumberInput } from '../src/features/calibration/CalibrationWorkbench';

function EditingHarness({ clearBeforeEffects = false }: { clearBeforeEffects?: boolean }) {
  const [value, setValue] = useState(92);
  useLayoutEffect(() => {
    if (!clearBeforeEffects) return;
    const input = screen.getByRole('spinbutton', { name: 'Rate' });
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
    setter.call(input, '');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }, [clearBeforeEffects]);
  return <>
    <CalibrationNumberInput label="Rate" value={value} min={0.01} step={1} onChange={setValue} />
    <output aria-label="Stored rate">{value}</output>
  </>;
}

test('does not restore the initial value over an edit before passive effects run', async () => {
  const user = userEvent.setup();
  await act(async () => { render(<EditingHarness clearBeforeEffects />); });
  const input = screen.getByRole('spinbutton', { name: 'Rate' });
  expect(input).toHaveValue(null);
  await user.type(input, '240');
  expect(input).toHaveValue(240);
  expect(screen.getByLabelText('Stored rate')).toHaveTextContent('240');
});

test('preserves decimal edits and restores only an empty field on blur', async () => {
  const user = userEvent.setup();
  render(<EditingHarness />);
  const input = screen.getByRole('spinbutton', { name: 'Rate' });
  await user.clear(input);
  await user.type(input, '0.25');
  expect(input).toHaveValue(0.25);
  expect(screen.getByLabelText('Stored rate')).toHaveTextContent('0.25');
  await user.clear(input);
  expect(input).toHaveValue(null);
  await user.tab();
  expect(input).toHaveValue(0.25);
});

test('synchronizes an externally loaded value before the next edit', async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  const view = render(<CalibrationNumberInput label="Rate" value={92} min={0.01} step={1} onChange={onChange} />);
  const input = screen.getByRole('spinbutton', { name: 'Rate' });
  await user.clear(input);
  view.rerender(<CalibrationNumberInput label="Rate" value={120} min={0.01} step={1} onChange={onChange} />);
  expect(input).toHaveValue(120);
  await user.clear(input);
  await user.type(input, '240');
  expect(input).toHaveValue(240);
  expect(onChange).toHaveBeenLastCalledWith(240);
});