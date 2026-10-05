# Navigation AI: Teaching Workflows and Page Alignment

> **English** | [繁體中文](./ai-navigation-teaching-workflows.zh-TW.md)

The class-creation guide follows the order of the real wizard: Schedule → Students → Teaching environment → Weekly tasks → Confirm and create.
"Create a course / class / classroom" defaults to class creation; the environment flow is entered only when the user explicitly asks to create an environment. A new request takes priority over the existing conversation.
Every flow card keeps a single short hint (28 characters or fewer); page descriptions and general answers are brief by default and expand only on follow-up questions.
A teaching environment that is already published and offered as a formal course can be reused directly; otherwise the environment is created from step 3, and the guide returns to the original class after publishing.
A machine template stores the software and settings of a single machine and is an optional source for an environment; an environment can also use an image, so templates do not have to be rebuilt every time a class is opened.

Creating a teaching environment on its own follows the real editor page: Basic information → Machine configuration → confirm publish and lock on the Machine configuration tab.
Guide buttons switch to the matching tab, keep the existing environment ID and the return-to-class URL, and do not add duplicate explanation messages.
After publishing, the environment is offered as a formal course, quick practice, or both, depending on how it is applied; the guide returns to the class only when it was entered from a class, and standalone creation does not require opening a class afterwards.
When asked what to do next, the guide answers based on the name, the current tab, the number of machines and the publish status, instead of repeating the whole flow.

`/ai/navigation/resolve` keeps the existing single-flow fields and adds `flows` and `answer`.
The model may return several flows and an answer to a question at the same time; flow steps are still generated from the server-side catalog, deduplicated and filtered by permission.
A plain interjected question uses `action=answer` and does not replace the flow in progress.

Within one conversation, the frontend keeps each flow's requirements, the configuration questionnaire and the saved class URL, and offers switching between flows.
The questionnaire only accepts the relevant turns; an interjected question is never saved as the purpose or the usage period.
Clearing the conversation or reloading the page clears this conversation memory; formal class data is still persisted by the existing APIs.

The class-creation and environment-editing pages expose explicitly selected, non-sensitive state through `useAiScreen`.
The backend accepts only fields that match the current path, the account's permissions and the screen definition; sensitive values never enter the navigation prompt.
If the page or its state changes while an answer is pending, the user is prompted to ask again, and the stale answer is not applied.
Visiting a page does not mean anything was submitted or created; the guide never performs writes to classes, templates or environments on its own.

Regression scenarios:

- "Create a template first, then a teaching environment, then open the class" keeps three switchable flows.
- "What is the difference between a teaching environment and a class?" answers with the relationship and does not open the machine configuration questionnaire.
- After asking "What is DNS?" in the middle of configuration, the original question about the usage period can still be answered.
- Leaving `/class-setup?classId=42&step=3` temporarily and coming back to the guide keeps the original class URL.
- Students cannot obtain teacher or administrator flows; unknown paths, flows or fields are never used as a source of actions.
