/*
 * Human-readable descriptions of timeline rows (see runTimeline.js for the row shape).
 *
 * Built only from facts the event carried, most reliable first: the author's own
 * "# STEP:" description, then the element's safe accessible name / label /
 * placeholder, then a safe locator, then a neutral fallback. A tag alone never
 * becomes a business action: a bare <button> is "Click button", not "Click Sign In".
 * Everything returned is plain text for React to render (never HTML).
 */
const KIND = {
  open: "open", navigate: "open", get: "open", refresh: "refresh", back: "back", forward: "forward",
  setwindowsize: "resize", click: "click", type: "type", sendkeys: "type", clear: "clear",
  actionchains: "actions", wait: "wait", assert: "assert", find: "find",
};

// The Selenium call behind each kind, for the technical details.
const METHOD = {
  open: "get", refresh: "refresh", back: "back", forward: "forward", resize: "set_window_size",
  click: "click", type: "send_keys", clear: "clear", actions: "ActionChains.perform",
  wait: "WebDriverWait.until", assert: "assert", find: "find_element",
};

const ROLE_WORD = {
  button: "button", link: "link", textbox: "field", checkbox: "checkbox", radio: "radio button",
  combobox: "dropdown", slider: "slider",
};
const TAG_WORD = { a: "link", button: "button", input: "field", textarea: "field", select: "dropdown" };

const WAIT_PHRASE = {
  presence_of_element_located: "for element to exist",
  presence_of_all_elements_located: "for elements to exist",
  visibility_of_element_located: "for element to be visible",
  visibility_of: "for element to be visible",
  element_to_be_clickable: "for element to be clickable",
  invisibility_of_element_located: "for element to disappear",
  invisibility_of_element: "for element to disappear",
  staleness_of: "for element to go stale",
  text_to_be_present_in_element: "for text to appear",
  title_contains: "for the page title to match",
  title_is: "for the page title to match",
  url_contains: "for the URL to match",
  url_to_be: "for the URL to match",
  url_matches: "for the URL to match",
  alert_is_present: "for an alert",
  frame_to_be_available_and_switch_to_it: "for a frame",
};

const FAILURE_SUMMARY = {
  NoSuchElementException: "The element could not be found",
  TimeoutException: "Timed out waiting",
  ElementClickInterceptedException: "Another element blocked the click",
  ElementNotInteractableException: "The element could not be used",
  StaleElementReferenceException: "The element changed before it could be used",
  InvalidSelectorException: "The locator is not valid",
  AssertionError: "The assertion did not hold",
  WebDriverException: "The browser reported an error",
};

export function kindOf(action) {
  return KIND[String(action || "").toLowerCase()] || "other";
}

function lowerFirst(text) {
  // "Email address" -> "email address"; acronyms ("SSN", "OTP code") stay as written.
  return text.length > 1 && text[0] === text[0].toUpperCase() && text[1] === text[1].toLowerCase()
    ? text[0].toLowerCase() + text.slice(1)
    : text;
}

function elementWord(row) {
  return ROLE_WORD[row.role] || TAG_WORD[String(row.target || "").toLowerCase()] || "element";
}

export function locatorText(row) {
  const value = row.locatorValue;
  if (!row.locatorBy || !value) return "";
  switch (row.locatorBy) {
    case "id": return `#${value}`;
    case "name": return `name=${value}`;
    case "css selector": return value;
    case "xpath": return `xpath ${value}`;
    case "link text": return `link "${value}"`;
    case "partial link text": return `link containing "${value}"`;
    case "class name": return `.${value}`;
    case "tag name": return `<${value}>`;
    default: return "";
  }
}

function withLocator(text, row) {
  const locator = locatorText(row);
  return locator ? `${text} (${locator})` : text;
}

/** The generic sentence for a step, without the author's description. */
export function genericTitle(row) {
  const word = elementWord(row);
  switch (kindOf(row.action)) {
    case "click":
      return row.label ? `Click "${row.label}"` : withLocator(`Click ${word}`, row);
    case "type":
      if (row.label) return `Enter ${lowerFirst(row.label)}`;
      // A placeholder often already reads like an instruction ("Enter your email"), so it is quoted.
      if (row.hint) return `Type into "${row.hint}" field`;
      if (row.sensitive) return withLocator("Enter sensitive value", row);
      return withLocator(`Type into ${word}`, row);
    case "clear":
      return row.label ? `Clear ${lowerFirst(row.label)}` : withLocator(`Clear ${word}`, row);
    case "open":
      return row.page ? `Open ${row.page}` : "Open page";
    case "refresh": return "Refresh page";
    case "back": return "Go back";
    case "forward": return "Go forward";
    case "resize":
      return row.windowSize ? `Resize window to ${row.windowSize.replace("x", "×")}` : "Resize window";
    case "actions": return "Perform mouse or keyboard actions";
    case "wait": {
      const phrase = row.waitUntilNot
        ? "until the condition is no longer met"
        : WAIT_PHRASE[row.waitCondition] || "for a condition";
      return withLocator(`Wait ${phrase}`, row);
    }
    case "assert":
      return row.sourceLine ? `Assertion failed (line ${row.sourceLine})` : "Assertion failed";
    case "find": return withLocator("Element not found", row);
    default: {
      const name = String(row.action || "").trim();
      return name ? name[0].toUpperCase() + name.slice(1) : "Step";
    }
  }
}

export function failureSummary(category) {
  return FAILURE_SUMMARY[category] || "The step failed";
}

/**
 * Everything the timeline shows for one row.
 *   title      the author's description if there is one, else the generic sentence
 *   subtitle   the generic sentence when a description replaced it, or a wait's limit
 *   details    [{ label, value }] technical facts for the expandable panel
 * Typed text is never part of an event, so "Typed value" is always "hidden".
 */
export function describeStep(row) {
  const kind = kindOf(row.action);
  const generic = genericTitle(row);
  const title = row.description || generic;
  let subtitle = row.description ? generic : "";
  if (kind === "wait" && row.waitTimeout != null) {
    subtitle = [subtitle, `up to ${row.waitTimeout} s`].filter(Boolean).join(" · ");
  }

  const details = [{ label: "Action", value: METHOD[kind] || String(row.action || "") }];
  const element = [row.target, row.role && row.role !== row.target ? `role ${row.role}` : ""]
    .filter(Boolean)
    .join(", ");
  if (element) details.push({ label: "Element", value: element });
  if (row.label) details.push({ label: "Label", value: row.label });
  if (row.hint) details.push({ label: "Placeholder", value: row.hint });
  const locator = locatorText(row);
  if (locator) details.push({ label: "Locator", value: locator });
  if (kind === "type") {
    details.push({ label: "Typed value", value: "hidden" });
    if (row.sensitive) details.push({ label: "Field", value: "treated as sensitive" });
  }
  if (row.page) details.push({ label: "Page", value: row.page });
  if (kind === "wait" && row.waitCondition) details.push({ label: "Condition", value: row.waitCondition });
  if (row.sourceLine) details.push({ label: "Line", value: String(row.sourceLine) });
  if (row.errorCategory) details.push({ label: "Exception", value: row.errorCategory });
  return { title, subtitle, details };
}
