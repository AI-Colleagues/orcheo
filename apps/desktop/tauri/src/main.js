const { invoke } = window.__TAURI__.core

const isChatKitSettings = new URLSearchParams(window.location.search).get('view') === 'chatkit-settings'

function nextFrame() {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve))
  })
}

if (isChatKitSettings) {
  const startupView = document.querySelector('#startup-view')
  const settingsView = document.querySelector('#settings-view')
  const settingsDetail = document.querySelector('#settings-detail')
  const signingKey = document.querySelector('#signing-key')
  const saveButton = document.querySelector('#save-signing-key')
  const removeButton = document.querySelector('#remove-signing-key')

  startupView.hidden = true
  settingsView.hidden = false

  function renderSettingsDetail(message) {
    settingsDetail.textContent = message
  }

  async function loadSettings() {
    try {
      const configured = await invoke('chatkit_signing_key_configured')
      renderSettingsDetail(
        configured
          ? 'A signing key is saved. Enter a replacement key, or remove the saved key.'
          : 'No signing key is saved. Enter one to enable ChatKit sessions.',
      )
    } catch (error) {
      renderSettingsDetail(typeof error === 'string' ? error : String(error))
    }
  }

  async function applyChange(command, message) {
    saveButton.disabled = true
    removeButton.disabled = true
    renderSettingsDetail(message)
    await nextFrame()
    try {
      await invoke(command, command === 'save_chatkit_signing_key' ? { key: signingKey.value } : {})
    } catch (error) {
      renderSettingsDetail(typeof error === 'string' ? error : String(error))
      saveButton.disabled = false
      removeButton.disabled = false
      return
    }
    try {
      await window.__TAURI__.window.getCurrentWindow().close()
    } catch {
      window.close()
    }
  }

  saveButton.addEventListener('click', () =>
    applyChange('save_chatkit_signing_key', 'Saving key and restarting local services...'),
  )
  removeButton.addEventListener('click', () =>
    applyChange('remove_chatkit_signing_key', 'Removing key and restarting local services...'),
  )
  loadSettings()
} else {
  const title = document.querySelector('#title')
  const detail = document.querySelector('#detail')
  const actions = document.querySelector('#actions')
  const restartButton = document.querySelector('#restart')
  const logsButton = document.querySelector('#logs')

  function renderStatus(nextTitle, nextDetail, { showActions = false } = {}) {
    title.textContent = nextTitle
    detail.textContent = nextDetail
    actions.hidden = !showActions
  }

  async function start() {
    renderStatus('Starting Orcheo', '')
    await nextFrame()
    try {
      const status = await invoke('start_orcheo')
      window.location.replace(status.backendUrl)
    } catch (error) {
      renderStatus('Orcheo could not start', typeof error === 'string' ? error : String(error), {
        showActions: true,
      })
    }
  }

  restartButton.addEventListener('click', async () => {
    restartButton.disabled = true
    renderStatus('Restarting Orcheo', 'Stopping and relaunching local services...')
    await nextFrame()
    try {
      const status = await invoke('restart_orcheo')
      window.location.replace(status.backendUrl)
    } catch (error) {
      renderStatus('Orcheo could not restart', typeof error === 'string' ? error : String(error), {
        showActions: true,
      })
    } finally {
      restartButton.disabled = false
    }
  })

  logsButton.addEventListener('click', async () => {
    try {
      await invoke('open_logs')
    } catch (error) {
      renderStatus('Could not open logs', typeof error === 'string' ? error : String(error), {
        showActions: true,
      })
    }
  })

  start()
}
