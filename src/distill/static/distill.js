// Progressive enhancement: forms, navigation, and native audio still work without JavaScript.
(() => {
    const articleBack = document.querySelector('[data-article-back]');
    if (articleBack && document.referrer) {
        const previous = new URL(document.referrer);
        if (previous.origin === location.origin && previous.pathname === '/') {
            articleBack.href = previous.href;
            articleBack.addEventListener('click', event => {
                if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || history.length < 2) return;
                event.preventDefault();
                history.back();
            });
        }
    }

    const modes = document.querySelector('.episode-modes');
    if (modes) {
        const forms = { weekly: document.querySelector('#weekly-form'), custom: document.querySelector('#custom-form') };
        function selectMode(mode) {
            for (const [name, form] of Object.entries(forms)) form.hidden = name !== mode;
            modes.querySelectorAll('button').forEach(button => {
                button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
            });
        }
        modes.hidden = false;
        document.querySelectorAll('.mode-title').forEach(title => { title.hidden = true; });
        modes.addEventListener('click', event => {
            const button = event.target.closest('[data-mode]');
            if (button) selectMode(button.dataset.mode);
        });
        selectMode('weekly');
    }

    const players = [...document.querySelectorAll('.episode-row audio')];
    const time = seconds => {
        if (!Number.isFinite(seconds)) return '—';
        const value = Math.max(0, Math.floor(seconds));
        return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
    };
    for (const audio of players) {
        const row = audio.closest('.episode-row');
        const play = row.querySelector('.episode-play');
        const scrub = row.querySelector('.episode-scrub');
        const seek = scrub.querySelector('input');
        const clock = scrub.querySelector('.episode-time');
        const error = row.querySelector('.playback-error');
        const title = row.dataset.title;
        const update = () => {
            const duration = audio.duration;
            const ready = Number.isFinite(duration) && duration > 0;
            seek.disabled = !ready;
            seek.max = ready ? duration : 0;
            seek.value = audio.currentTime || 0;
            seek.setAttribute('aria-valuetext', `${time(audio.currentTime)} of ${time(duration)}`);
            clock.textContent = `${time(audio.currentTime)} / ${time(duration)}`;
            const playing = !audio.paused && !audio.ended;
            row.dataset.playing = String(playing);
            play.setAttribute('aria-label', `${playing ? 'Pause' : 'Play'} ${title}`);
        };
        const fallback = () => {
            const restoreFocus = document.activeElement === play;
            audio.hidden = false;
            play.hidden = true;
            scrub.hidden = true;
            row.classList.remove('enhanced-player');
            error.textContent = 'Playback could not start. Try the audio controls.';
            error.hidden = false;
            if (restoreFocus) audio.focus();
        };
        play.addEventListener('click', async () => {
            if (!audio.paused) {
                audio.pause();
                return;
            }
            error.hidden = true;
            for (const other of players) if (other !== audio) other.pause();
            if (audio.ended) audio.currentTime = 0;
            try {
                await audio.play();
            } catch (failure) {
                // A quick second click or starting another episode can cancel a pending play.
                if (failure.name !== 'AbortError') fallback();
            }
        });
        audio.addEventListener('play', () => {
            for (const other of players) if (other !== audio) other.pause();
            update();
        });
        for (const event of ['loadedmetadata', 'durationchange', 'timeupdate', 'pause', 'ended']) {
            audio.addEventListener(event, update);
        }
        // Errors on a nested <source> do not bubble; capture them as well.
        audio.addEventListener('error', fallback, true);
        seek.addEventListener('input', () => {
            if (!seek.disabled) audio.currentTime = Number(seek.value);
            update();
        });
        audio.hidden = true;
        play.hidden = false;
        scrub.hidden = false;
        row.classList.add('enhanced-player');
        update();
        if (audio.error) fallback();
    }

    // Native dialogs provide focus containment, Escape to close, and focus restoration.
    const commands = document.querySelector('#navigation-dialog');
    const help = document.querySelector('#shortcuts-dialog');
    document.querySelectorAll('[data-open-dialog]').forEach(button => {
        button.hidden = false;
        button.addEventListener('click', () => document.getElementById(button.dataset.openDialog).showModal());
    });
    document.querySelectorAll('dialog').forEach(dialog => {
        dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    });
    const refresh = document.querySelector('[data-refresh]');
    if (refresh) {
        refresh.hidden = false;
        refresh.addEventListener('click', () => window.location.reload());
    }
    document.addEventListener('keydown', event => {
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
            event.preventDefault();
            if (help.open) help.close();
            if (commands.open) commands.close(); else commands.showModal();
        }
    });
})();
