import AppearanceSettingsTab from "@features/account/components/settings/appearance-settings-tab";

export default function Settings() {
  return (
    <main className="h-full min-h-0 overflow-auto">
      <div className="mx-auto flex w-full max-w-7xl flex-col space-y-4 p-8 pt-6">
        <div className="flex items-center justify-between space-y-2">
          <h2 className="text-3xl font-bold tracking-tight">Settings</h2>
        </div>
        <AppearanceSettingsTab />
      </div>
    </main>
  );
}
