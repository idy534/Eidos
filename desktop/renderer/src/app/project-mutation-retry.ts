/** Retain an operation ID when an unchanged form is retried after a lost reply. */
export class ProjectMutationRetry {
  private current: { key: string; operationId: string } | undefined;

  prepare<T extends object>(command: T): T & { operationId: string } {
    const key = JSON.stringify(command);
    if (this.current?.key !== key) this.current = { key, operationId: crypto.randomUUID() };
    return { ...command, operationId: this.current.operationId };
  }

  reset(): void { this.current = undefined; }
}
