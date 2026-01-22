export type TaskPropertyNameEnv = {
  TASK_STATUS_PROPERTY_NAME?: string;
  TASK_DONE_DATE_PROPERTY_NAME?: string;
  TASK_DROP_DATE_PROPERTY_NAME?: string;
};

export const DEFAULT_TASK_STATUS_PROPERTY_NAME = "Status";
export const DEFAULT_TASK_DONE_DATE_PROPERTY_NAME = "Done date";
export const DEFAULT_TASK_DROP_DATE_PROPERTY_NAME = "Drop date";

export function getTaskPropertyNames(env: TaskPropertyNameEnv) {
  const statusPropertyName =
    env.TASK_STATUS_PROPERTY_NAME || DEFAULT_TASK_STATUS_PROPERTY_NAME;
  const doneDatePropertyName =
    env.TASK_DONE_DATE_PROPERTY_NAME || DEFAULT_TASK_DONE_DATE_PROPERTY_NAME;
  const dropDatePropertyName =
    env.TASK_DROP_DATE_PROPERTY_NAME || DEFAULT_TASK_DROP_DATE_PROPERTY_NAME;
  return { statusPropertyName, doneDatePropertyName, dropDatePropertyName };
}
