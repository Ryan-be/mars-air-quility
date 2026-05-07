/**
 * Settings → Grow page orchestrator.
 *
 * Mounts the three independent panel components into the slots
 * provided by templates/grow_settings.html. Each component fetches
 * its own data on mount; this entry point doesn't share any state
 * across panels.
 */
import { renderEnrollmentKeyRotator } from
  "./components/enrollment-key-rotator.mjs";
import { renderPlantProfilesEditor } from
  "./components/plant-profiles-editor.mjs";
import { renderHolidayModeToggle } from
  "./components/holiday-mode-toggle.mjs";


const keySlot = document.getElementById("grow-settings-key");
const profilesSlot = document.getElementById("grow-settings-profiles");
const holidaySlot = document.getElementById("grow-settings-holiday");

if (keySlot) {
  keySlot.appendChild(renderEnrollmentKeyRotator());
}
if (profilesSlot) {
  profilesSlot.appendChild(renderPlantProfilesEditor());
}
if (holidaySlot) {
  holidaySlot.appendChild(renderHolidayModeToggle());
}
