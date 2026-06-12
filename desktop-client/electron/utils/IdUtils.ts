import { randomUUID } from "node:crypto";

class IdUtils {
  public static genUUID() {
    return randomUUID();
  }
}

export default IdUtils;
