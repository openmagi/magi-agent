export {};

declare global {
  interface Window {
    google: {
      picker: {
        PickerBuilder: new () => GooglePickerBuilder;
        ViewId: { DOCS: string; SPREADSHEETS: string; FOLDERS: string };
        Feature: { MULTISELECT_ENABLED: string };
        Action: { PICKED: string; CANCEL: string };
      };
    };
    gapi: {
      load: (api: string, callback: () => void) => void;
    };
  }

  interface GooglePickerBuilder {
    setOAuthToken(token: string): GooglePickerBuilder;
    setAppId(appId: string): GooglePickerBuilder;
    addView(viewId: string): GooglePickerBuilder;
    enableFeature(feature: string): GooglePickerBuilder;
    setCallback(callback: (data: GooglePickerCallbackData) => void): GooglePickerBuilder;
    build(): { setVisible(visible: boolean): void };
  }

  interface GooglePickerCallbackData {
    action: string;
    docs?: Array<{
      id: string;
      name: string;
      mimeType: string;
      iconUrl?: string;
    }>;
  }
}
