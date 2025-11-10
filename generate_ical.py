import httpx
from icalendar import Calendar, Event
from datetime import date, datetime, timedelta, time
import pytz
import sys

# --- Configuration ---
API_URL = "https://www.stuttgarterbaeder.de/fileadmin/jsonData/baeder.json"
OUTPUT_FILE = "hallenbaeder.ics"
DAYS_TO_GENERATE = 14  # Generate for the next two weeks
TIMEZONE = pytz.timezone("Europe/Berlin")
# ---------------------

def get_opening_hours(pool, target_date):
    """
    Finds the opening hours for a specific pool on a specific date.
    Checks holiday/special hours first, then regular hours.
    Returns (start_time, end_time) or None
    """
    day_of_week_str = target_date.strftime('%a')[:2].lower()  # 'mo', 'di', etc.
    business_hours = pool.get('businesshours', {})
    
    # 1. Check holiday/special hours first (they override regular hours)
    holiday_hours = business_hours.get('holiday_bhpool', [])
    for entry in holiday_hours:
        try:
            valid_from = date.fromisoformat(entry['validity']['from'])
            valid_to = date.fromisoformat(entry['validity']['to'])
            
            if valid_from <= target_date <= valid_to:
                # This entry applies to our target date
                
                # Check if explicitly closed
                if entry.get('closed', False):
                    return None  # Closed for holiday/maintenance

                # Check for specific hours for this day
                day_hours = entry.get(day_of_week_str)
                if day_hours and day_hours.get('from'):
                    return day_hours['from'], day_hours['to']
                
                # If no specific hours for this day, but not marked 'closed',
                # it might be a holiday with no special hours,
                # but we should stop checking (don't fall back to regular).
                # However, the data seems to use 'closed: true' or specific times.
                # If it's not closed and has no time, it's safer to assume closed.
                return None 

        except (ValueError, TypeError):
            continue # Skip invalid date entries

    # 2. If no holiday rule found, check regular "usually" hours
    regular_hours = business_hours.get('usually_bhpool', [])
    for entry in regular_hours:
        try:
            valid_from = date.fromisoformat(entry['validity']['from'])
            valid_to = date.fromisoformat(entry['validity']['to'])

            if valid_from <= target_date <= valid_to:
                day_hours = entry.get(day_of_week_str)
                if day_hours and day_hours.get('from'):
                    return day_hours['from'], day_hours['to']
                else:
                    return None  # Regularly closed on this day
        
        except (ValueError, TypeError):
            continue # Skip invalid date entries

    return None # No matching schedule found

def create_calendar():
    """
    Fetches pool data and generates an iCalendar file.
    """
    try:
        response = httpx.get(API_URL)
        response.raise_for_status()
        pools = response.json()
    except httpx.RequestError as e:
        print(f"Error fetching API: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error parsing JSON: {e}", file=sys.stderr)
        sys.exit(1)

    cal = Calendar()
    cal.add('prodid', '-//Stuttgart Hallenbad Calendar//')
    cal.add('version', '2.0')
    cal.add('name', 'Stuttgarter Hallenbäder')
    cal.add('X-WR-CALNAME', 'Stuttgarter Hallenbäder')
    cal.add('description', 'Öffnungszeiten der Stuttgarter Hallenbäder')
    cal.add('X-WR-CALDESC', 'Öffnungszeiten der Stuttgarter Hallenbäder')

    today = date.today()
    
    for pool in pools:
        # Filter for "Hallenbad"
        pool_type = pool.get('lookups', {}).get('type', {}).get('value')
        if pool_type != 'Hallenbad':
            continue

        pool_name = pool.get('name')
        if not pool_name:
            continue
            
        print(f"Processing: {pool_name}")
        
        building = pool.get('building', {})
        location = f"{building.get('street', '')}, {building.get('zip_code', '')} {building.get('city', '')}"

        # Loop for the next N days
        for i in range(DAYS_TO_GENERATE):
            current_date = today + timedelta(days=i)
            hours = get_opening_hours(pool, current_date)
            
            if hours:
                start_str, end_str = hours
                try:
                    start_time = time.fromisoformat(start_str)
                    end_time = time.fromisoformat(end_str)
                    
                    dt_start = TIMEZONE.localize(datetime.combine(current_date, start_time))
                    dt_end = TIMEZONE.localize(datetime.combine(current_date, end_time))

                    # Handle cases where end time is next day (e.g., 23:00-01:00)
                    # The data doesn't seem to have this, but it's good practice.
                    if dt_end <= dt_start:
                         dt_end += timedelta(days=1)

                    event = Event()
                    event.add('summary', pool_name)
                    event.add('dtstart', dt_start)
                    event.add('dtend', dt_end)
                    event.add('dtstamp', datetime.now(pytz.utc))
                    event.add('location', location)
                    event.add('uid', f"{pool['id']}-{current_date.isoformat()}@stuttgarterbaeder.de")
                    cal.add_component(event)

                except ValueError as e:
                    print(f"  Skipping invalid time for {pool_name} on {current_date}: {e}", file=sys.stderr)

    # Save the calendar file
    try:
        with open(OUTPUT_FILE, 'wb') as f:
            f.write(cal.to_ical())
        print(f"\nSuccessfully generated {OUTPUT_FILE}")
    except IOError as e:
        print(f"Error writing file: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    create_calendar()